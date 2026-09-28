<#
.SYNOPSIS
  A7A SD 卡离线修复（Windows 侧）—— 断电拔卡后，在读卡器里跑 e2fsck。

.DESCRIPTION
  为什么必须离线修：根分区此刻正挂载着，e2fsck 不能安全地修一个已挂载的 ext4。
  项目自己的结论也是「先断电拔卡、离线 e2fsck -fy，确认干净了再上机」。

  这个脚本把「有风险的顺序」固定下来，默认只体检、不动任何东西：

    -Check（默认，不需要管理员，零风险）
      1. 确认 WSL 发行版与 e2fsck 可用
      2. 找到读卡器里那张卡，核对容量
      3. 打印将要执行的完整命令

    -Repair（需要管理员）
      1. wsl --mount <disk> --bare     只把盘交给 WSL，不自动挂载（关键）
      2. e2image -Q                     存一份元数据快照，作为修复前的取证参照
      3. e2fsck -fn                     只读体检，完整报告存档
      4. 【要你手打磁盘号确认】          —— 这一步是故意的，不能 -Yes 跳过
      5. e2fsck -fy                     真正修复
      6. e2fsck -fn                     再体检一次，确认干净
      7. wsl --unmount

    -PatchBoot -Model A7A|A7S|A7Z（需要管理员，修完之后再跑）
      只读探测 /boot/extlinux/extlinux.conf 的 fdtdir 现状，并打印要手打的 sed。
      **故意不自动改** —— fdtfile 写错会起不来，必须人工确认丝印。

.NOTES
  为什么不做整卡裸镜像：本机 C: 剩 55.4G / D: 剩 31.1G，而卡是 62.5G，放不下。
  数据已经用 rescue1/rescue2 两个包抢救出来并双向 md5 核对过了
  （D:\Code\radxa\a7a-rescue-2026-09-28\），所以退路是「重刷」而不是「回滚镜像」。

  写这个脚本时踩到的坑（别再犯）：
    * PowerShell here-string 里，行尾的反引号是续行符，会把换行吃掉，
      于是下一行的 "@ 不再位于行首 → "missing the terminator"。本脚本已不用 here-string。
    * `ls /dev/sd* | tail -1` 拿到的是**分区**（/dev/sda3），不是整盘（/dev/sda）。
      必须用 lsblk -d 过滤 TYPE=="disk"。
    * wsl.exe 自身输出是 UTF-16，直接打印会乱码；取数据一律只读 bash 的 stdout。

.EXAMPLE
  .\sd_offline_repair.ps1                    # 只看，不动

.EXAMPLE
  .\sd_offline_repair.ps1 -Repair            # 管理员 PowerShell 里跑

.EXAMPLE
  .\sd_offline_repair.ps1 -PatchBoot -Model A7S
#>

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Repair,
    [switch]$PatchBoot,
    [ValidateSet('A7A', 'A7S', 'A7Z')]
    [string]$Model,
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LogDir = "$PSScriptRoot\..\..\..\a7a-rescue-2026-09-28\repair-logs"
)

$ErrorActionPreference = 'Stop'

function Say  { param([string]$m) Write-Host $m }
function Head { param([string]$m) Write-Host ""; Write-Host "-- $m " -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  OK   $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  WARN $m" -ForegroundColor Yellow }
function Bad  { param([string]$m) Write-Host "  FAIL $m" -ForegroundColor Red }

# ── WSL 调用 ─────────────────────────────────────────────────────────
# 注意：wsl.exe 自己的提示信息是 UTF-16，PowerShell 里会乱码。
# 取数据时只读 bash 的 stdout，所以把 stderr 丢掉，避免污染解析。
function Invoke-Wsl {
    param([Parameter(Mandatory)][string]$Command, [switch]$WithStderr)
    # 不要用 $args（PowerShell 自动变量），改名 wslArgs
    $wslArgs = @('-d', $Distro, '-u', 'root', '--', 'bash', '-lc', $Command)
    if ($WithStderr) { return @(& wsl @wslArgs 2>&1) }
    return @(& wsl @wslArgs 2>$null)
}

# wsl.exe 自身输出是 UTF-16：临时切控制台编码读它，读完立刻还原，
# 否则后面读 bash 的 stdout 会被按 UTF-16 解成乱码。
function Invoke-WslExe {
    param([string[]]$WslArgs)
    $saved = $null
    try { $saved = [Console]::OutputEncoding } catch { }
    try {
        try { [Console]::OutputEncoding = [System.Text.Encoding]::Unicode } catch { }
        & wsl @WslArgs
        $code = $LASTEXITCODE
    } finally {
        if ($saved) { try { [Console]::OutputEncoding = $saved } catch { } }
    }
    return $code
}

# ── 路径换算 ─────────────────────────────────────────────────────────
function ConvertTo-WslPath {
    param([Parameter(Mandatory)][string]$WinPath)
    $p = (Resolve-Path -LiteralPath $WinPath).Path
    $drive = $p.Substring(0, 1).ToLower()
    return '/mnt/' + $drive + ($p.Substring(2) -replace '\\', '/')
}

# ── 找卡（Windows 侧）────────────────────────────────────────────────
function Get-SdDisk {
    $usb = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' })
    if ($usb.Count -eq 0) {
        Bad '没有 USB 磁盘。读卡器没插？'
        return $null
    }
    foreach ($d in $usb) {
        if ($d.Size -eq 0 -or $d.OperationalStatus -ne 'Online') {
            Warn "磁盘 $($d.Number)：$($d.FriendlyName) / $($d.OperationalStatus) / 容量 $($d.Size) —— 介质没插好？"
        }
    }
    $card = $usb | Where-Object { $_.Size -gt 1GB -and $_.OperationalStatus -eq 'Online' } |
            Select-Object -First 1
    if (-not $card) {
        Bad 'USB 磁盘里没有「有介质且在线」的卡。把 SD 卡插进读卡器再跑。'
        return $null
    }
    return $card
}

# ── 找盘（WSL 侧）：只列整盘，不列分区 ──────────────────────────────
function Get-SdDiskNodes {
    $raw = Invoke-Wsl -Command 'lsblk -dpno NAME,TYPE | awk ''$2=="disk" && $1 ~ /^\/dev\/sd/ {print $1}'''
    return @($raw | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^/dev/sd' })
}

# ── 在指定整盘上挑「最大的 ext4 分区」当根分区 ──────────────────────
function Get-RootPartition {
    param([Parameter(Mandatory)][string]$Disk)
    $raw = Invoke-Wsl -Command 'lsblk -bprno NAME,FSTYPE,SIZE | awk ''$2=="ext4" {print $1" "$3}'''
    $cands = @()
    foreach ($line in $raw) {
        $line = "$line".Trim()
        if (-not $line -or $line -notmatch '^/dev/') { continue }
        $f = $line -split '\s+'
        if ($f.Count -lt 2) { continue }
        if ($f[0] -like "$Disk*") {
            $cands += [pscustomobject]@{ Dev = $f[0]; Size = [int64]$f[1] }
        }
    }
    if ($cands.Count -eq 0) { return $null }
    return ($cands | Sort-Object Size -Descending | Select-Object -First 1)
}

# ── 前置检查 ─────────────────────────────────────────────────────────
function Test-Preflight {
    Head '0. 前置检查'

    $wslOk = $false
    try {
        $v = (Invoke-Wsl -Command 'echo WSL_OK') -join ''
        if ($v -match 'WSL_OK') { $wslOk = $true }
    } catch { }
    if ($wslOk) { Ok "WSL 发行版 $Distro 可用" }
    else { Bad "WSL 里跑不了命令（发行版名对不对？wsl -l -v 看看）"; return $false }

    $fsck = (Invoke-Wsl -Command 'command -v e2fsck fsck.ext4') -join ' '
    if ($fsck -match 'e2fsck') { Ok "e2fsck 在：$($fsck.Trim())" }
    else {
        Bad 'WSL 里没有 e2fsck。先装：'
        Say "       wsl -d $Distro -u root -- bash -lc 'apt-get update && apt-get install -y e2fsprogs'"
        return $false
    }

    $ver = (Invoke-Wsl -Command 'e2fsck -V 2>&1 | head -1') -join ''
    Ok "版本：$($ver.Trim())"
    return $true
}

# ── 计划展示 ─────────────────────────────────────────────────────────
function Show-Plan {
    param($Card, [string]$MetaDir)
    Head '将要执行的命令（-Repair 时）'
    $dp = "\\.\PHYSICALDRIVE$($Card.Number)"
    Say "  wsl --mount $dp --bare"
    Say '  # 之后在 WSL 里（盘符由脚本探测，这里写 /dev/sdX 示意）'
    Say "  e2image -Q /dev/sdX3 $MetaDir\meta-<时间戳>.e2i   # 元数据快照"
    Say '  e2fsck -fn /dev/sdX3                              # 只读体检'
    Say '  e2fsck -fy /dev/sdX3                              # 修复（需手打磁盘号确认）'
    Say '  e2fsck -fn /dev/sdX3                              # 复检'
    Say "  wsl --unmount $dp"
    Say ''
    Say '  这些命令**一个都不会碰 U-Boot**，只动 SD 卡上的 ext4。'
}

# ── 主流程 ───────────────────────────────────────────────────────────
Say '========================================================'
Say ' A7A SD 卡离线修复 —— 断电拔卡之后才能跑'
Say '========================================================'

if (-not (Test-Preflight)) { exit 1 }

Head '1. 找卡'
$card = Get-SdDisk
if (-not $card) { exit 1 }
Ok "磁盘 $($card.Number)：$($card.FriendlyName)，$([math]::Round($card.Size / 1GB, 1)) GiB，$($card.OperationalStatus)"

if ($card.Size -lt 50GB -or $card.Size -gt 70GB) {
    Warn '容量不是预期的 ~62.5 GiB —— 确认一下这是不是那块卡'
}

$diskPath = "\\.\PHYSICALDRIVE$($card.Number)"

# 默认（含 -Check）：只体检，不动
if (-not $Repair -and -not $PatchBoot) {
    Show-Plan -Card $card -MetaDir $LogDir
    Say ''
    Say '  上面是计划。要真的执行，用**管理员** PowerShell 加 -Repair。'
    Say '  现在这张卡还没插（或没插好），插好之后可以再跑一次 -Check 复核。'
    exit 0
}

# 下面都要动盘，必须管理员
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Bad '-Repair / -PatchBoot 需要管理员 PowerShell（wsl --mount 需要）'
    exit 1
}

# ── 交给 WSL（--bare）────────────────────────────────────────────────
Head '2. 把盘交给 WSL（--bare，不自动挂载）'
$before = Get-SdDiskNodes
Say "  挂载前 WSL 里的 sd 盘：$(if ($before) { $before -join ' ' } else { '(无)' })"

$code = Invoke-WslExe @('--mount', $diskPath, '--bare')
if ($code -ne 0) {
    Warn "wsl --mount 返回 $code"
    Say '  常见原因：'
    Say '    * Windows 已经给卡上的分区占了句柄（弹窗提示「需要格式化」时点取消，别格式化）'
    Say '    * 读卡器是 USB 可移动介质，某些 WSL 版本不支持 --bare 挂它'
    Say '  替代方案：用 VMware/VirtualBox 把读卡器直通给 Linux 虚拟机，在虚拟机里跑同样的 e2fsck。'
    exit 1
}
Start-Sleep -Seconds 3

$after = Get-SdDiskNodes
Say "  挂载后 WSL 里的 sd 盘：$(if ($after) { $after -join ' ' } else { '(无)' })"
$new = @($after | Where-Object { $before -notcontains $_ })
if ($new.Count -eq 0) {
    Bad '没看到新盘出现。'
    Say '  可能原因：读卡器是 USB 可移动介质，某些 WSL 版本不支持 --bare 挂它。'
    Say '  替代方案：用 VMware/VirtualBox 把读卡器直通给 Linux 虚拟机，在虚拟机里跑同样的 e2fsck。'
    Invoke-WslExe @('--unmount', $diskPath) | Out-Null
    exit 1
}
$dev = $new[0]
Ok "新盘 = $dev"

$rp = Get-RootPartition -Disk $dev
if (-not $rp) {
    Bad "在 $dev 上没找到 ext4 分区。"
    Say "  自己看一下：wsl -d $Distro -u root -- lsblk -f $dev"
    Invoke-WslExe @('--unmount', $diskPath) | Out-Null
    exit 1
}
$part = $rp.Dev
Ok "根分区 = $part（ext4，$([math]::Round($rp.Size / 1GB, 1)) GiB）"

# ── PatchBoot：只读探测 + 打印要手打的命令 ──────────────────────────
if ($PatchBoot) {
    if (-not $Model) {
        Bad '-PatchBoot 必须带 -Model A7A|A7S|A7Z（型号以板子正面丝印为准）'
        Invoke-WslExe @('--unmount', $diskPath) | Out-Null
        exit 1
    }
    Head "3. 探测 boot 分区现状（只读，目标型号 $Model）"
    $dtb = "sun60i-a733-cubie-$($Model.ToLower()).dtb"
    Say "  目标 dtb：/usr/lib/linux-image-6.6.98-4-aw2511/allwinner/$dtb"

    $mnt = '/mnt/a7a-boot'
    # 拼成单行 bash（不要用 here-string：行尾反引号会吃掉换行导致语法错）
    $probe = @(
        "mkdir -p $mnt"
        "mount -o ro $part $mnt"
        "CONF=$mnt/boot/extlinux/extlinux.conf"
        'ls -l $CONF'
        'echo "---- fdtdir 相关行 ----"'
        'grep -n -e fdtdir -e fdtfile $CONF'
        'echo "---- 全部内容 ----"'
        'cat $CONF'
        "umount $mnt"
    ) -join '; '
    Invoke-Wsl -Command $probe -WithStderr | ForEach-Object { Say "  $_" }

    Head '4. 确认无误后，手打下面三条'
    Say "  # 先备份再改（脚本不代劳）"
    Say "  wsl -d $Distro -u root -- bash -lc `"mkdir -p $mnt; mount -o rw $part $mnt; CONF=$mnt/boot/extlinux/extlinux.conf; cp -a `$CONF `$CONF.bak-prefdtfile; sed -i '/^[[:space:]]*fdtdir/i fdtfile /usr/lib/linux-image-6.6.98-4-aw2511/allwinner/$dtb' `$CONF; grep -n -e fdtdir -e fdtfile `$CONF; umount $mnt`""
    Say ''
    Warn '我故意不自动改 —— fdtfile 写错会起不来。请先确认丝印，再手打上面这条。'
    Invoke-WslExe @('--unmount', $diskPath) | Out-Null
    Ok '已 unmount'
    exit 0
}

# ── Repair ───────────────────────────────────────────────────────────
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$wslLog = ConvertTo-WslPath -WinPath $LogDir

Head '3. 元数据快照（修复前的取证参照）'
$meta = "$wslLog/meta-$stamp.e2i"
$mOut = Invoke-Wsl -Command "e2image -Q $part '$meta' 2>&1; echo `"RC=`$?`"; ls -l '$meta' 2>&1"
$mOut | ForEach-Object { Say "  $_" }
$metaOk = ($mOut -join "`n") -match 'RC=0'
if ($metaOk) { Ok "元数据快照：$LogDir\meta-$stamp.e2i" }
else { Warn '元数据快照没成功（坏文件系统上 e2image 失败是常见的）—— 继续，不阻塞' }

Head '4. 只读体检（e2fsck -fn，一个字都不写）'
$report = Join-Path $LogDir "e2fsck-before-$stamp.txt"
$out = Invoke-Wsl -Command "e2fsck -fn $part 2>&1"
$out | Out-File -FilePath $report -Encoding UTF8
$out | Select-Object -Last 25 | ForEach-Object { Say "  $_" }
Ok "完整报告：$report"

Head '5. 确认'
Say '  上面就是坏成什么样。修复会：重建块位图、把孤儿 inode 丢进 lost+found。'
Say '  数据已经抢救出来了（rescue1/rescue2 双向 md5 通过），所以最坏情况是重刷。'
$ans = Read-Host "  确认要修就打磁盘号 $($card.Number)，其他任何输入都中止"
if ("$ans".Trim() -ne "$($card.Number)") {
    Warn '已中止，什么都没改。'
    Invoke-WslExe @('--unmount', $diskPath) | Out-Null
    exit 0
}

Head '6. 修复（e2fsck -fy）'
$fixLog = Join-Path $LogDir "e2fsck-fix-$stamp.txt"
$out2 = Invoke-Wsl -Command "e2fsck -fy $part 2>&1"
$out2 | Out-File -FilePath $fixLog -Encoding UTF8
$out2 | Select-Object -Last 25 | ForEach-Object { Say "  $_" }
Ok "完整日志：$fixLog"

Head '7. 复检（e2fsck -fn）'
$out3 = Invoke-Wsl -Command "e2fsck -fn $part 2>&1"
$out3 | Out-File -FilePath (Join-Path $LogDir "e2fsck-after-$stamp.txt") -Encoding UTF8
$out3 | Select-Object -Last 15 | ForEach-Object { Say "  $_" }

$clean = ($out3 -join "`n") -notmatch 'Filesystem still has errors|EXT4-fs error|bad block bitmap|should not happen'
if ($clean) { Ok '复检通过 —— 文件系统干净了' } else { Warn '复检还有问题，看报告' }

Head '8. 收工'
Invoke-WslExe @('--unmount', $diskPath) | Out-Null
Ok '已 unmount'
Say ''
Say '  下一步（按顺序）：'
Say '    a) .\sd_offline_repair.ps1 -PatchBoot -Model <A7A|A7S|A7Z>   <- 先确认丝印！'
Say '    b) 卡插回板子，上电'
Say '    c) 起来之后立刻：sudo tune2fs -c 20 -i 7d /dev/mmcblk1p3    <- 打开周期 fsck'
Say '    d) 对照 docs\troubleshooting\a7a-2026-09-28-incident.md 第七节 逐条做完'
