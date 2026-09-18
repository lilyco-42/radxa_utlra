# flash-a7a.ps1 — Windows 一键把 Radxa A733 镜像写入 SD 卡
#
# 为什么需要它：
#   在 Windows 上刷 A7A 的卡有五个反复踩的坑，Etcher 一个都不告诉你：
#     1) 装过 usbipd → 读卡器被 USB/IP 抓走，系统"完全看不见"设备
#     2) 磁盘带 Linux 分区表 → Windows 自动把盘设为 Offline，Etcher 报
#        "The writer process ended unexpectedly"
#     3) 盘号认错 → 刷掉系统盘
#     4) 没有写后回读校验 → 坏卡/坏读卡器写进去看起来也是"成功"
#     5) 刷完忘了还要做防腐化配置（ro / DTB），下次启动又烂
#
# 这个脚本把 1~4 都处理掉，并在最后提示第 5 步。
#
# 用法（必须以管理员身份运行 PowerShell）：
#
#   # 只体检：看设备到底认没认出来，不做任何改动
#   .\scripts\flash-a7a.ps1 -Check
#
#   # 列出所有物理盘（人工核对目标）
#   .\scripts\flash-a7a.ps1 -List
#
#   # 干跑：选目标 + 解压 + 校验，不写盘
#   .\scripts\flash-a7a.ps1 -Image C:\Users\me\Downloads\xx.img.xz -DiskNumber 1 -DryRun
#
#   # 真刷（会要求输入 YES 确认）
#   .\scripts\flash-a7a.ps1 -Image C:\Users\me\Downloads\xx.img.xz -DiskNumber 1
#
#   # 刷完顺便把防腐化配置也写上（可选，见 -HardenArgs 参数说明）
#   .\scripts\flash-a7a.ps1 -Image xx.img.xz -DiskNumber 1 -Harden
#
# 安全设计：
#   - 拒绝刷任何 BusType 为 NVMe/SATA 的盘（只允许 USB 可移动盘）
#   - 拒绝刷含有 Windows 系统分区的盘
#   - 强制确认盘号 + 容量 + 序列号三者都打印出来
#   - 写后回读前 64 MiB 比对 SHA256

[CmdletBinding()]
param(
    [string]$Image,
    [int]$DiskNumber = -1,
    [switch]$List,
    [switch]$Check,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$SkipVerify,
    [switch]$Harden,
    [int]$VerifyMB = 64
)

$ErrorActionPreference = 'Stop'

# ─────────────────────────────────────────────────────────────
# 输出助手
# ─────────────────────────────────────────────────────────────
function Ok   ($m) { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Fail ($m) { Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Info ($m) { Write-Host "  [info] $m" -ForegroundColor Gray }
function Head ($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

function Die ($m) { Fail $m; exit 1 }

# ─────────────────────────────────────────────────────────────
# 管理员检查
# ─────────────────────────────────────────────────────────────
function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Die "需要管理员权限。请右键 PowerShell -> 以管理员身份运行，然后重新执行本脚本。"
    }
    Ok "管理员权限已确认"
}

# ─────────────────────────────────────────────────────────────
# 体检：usbipd / 设备是否被拦截
# ─────────────────────────────────────────────────────────────
function Invoke-Check {
    Head "USB 驱动栈体检（读卡器看不见时，先查这里）"

    # 1) usbipd：最常见的"完全静默"元凶
    $usbipdExe = "C:\Program Files\usbipd-win\usbipd.exe"
    if (Test-Path $usbipdExe) {
        Warn "检测到 usbipd-win 已安装"
        Info "它会把 USB 设备强制共享给 WSL/远程，导致主系统看不见读卡器"
        try {
            # 注意：usbipd 输出的中文设备名在 GBK 控制台下会乱码，
            # 而且 "Not shared" 里本身含 "shared" 子串 —— 必须按行精确匹配
            # 只看 STATE 列，否则会把所有设备都误判成"被共享"。
            $raw = & $usbipdExe list 2>&1 | Out-String
            Write-Host $raw

            $shared = @()
            foreach ($line in ($raw -split "`r?`n")) {
                # 一行形如： 7-2    349c:0418   <设备名>   Not shared
                if ($line -match '^\s*(\d+-\d+)\s+([0-9a-f]{4}:[0-9a-f]{4})\s+(.+?)\s+(Not shared|Shared.*|Attached.*|Shared \(forced\))\s*$') {
                    $busid = $Matches[1]; $vidpid = $Matches[2]; $state = $Matches[4]
                    if ($state -notmatch '^Not shared$') {
                        $shared += "$busid $vidpid ($state)"
                    }
                }
            }

            if ($shared.Count -gt 0) {
                Warn "有 $($shared.Count) 个设备处于 Shared/Attached 状态："
                $shared | ForEach-Object { Write-Host "        $_" -ForegroundColor Yellow }
                Warn "如果是你的读卡器（VID:PID 通常是 0bda/05e3/214b/349c），必须解绑："
                Write-Host ""
                Write-Host '      & "C:\Program Files\usbipd-win\usbipd.exe" unbind --all' -ForegroundColor White
                Write-Host '      net stop usbipd' -ForegroundColor White
                Write-Host '      sc.exe config usbipd start= demand' -ForegroundColor White
                Write-Host ""
            } else {
                Ok "usbipd 没有占用任何设备（所有设备都是 Not shared）"
            }
            if ($raw -match "hrdevmon") {
                Warn "usbipd 与反作弊驱动 hrdevmon 冲突（会导致 usbipd 自身加载失败 Code 37）"
                Warn "如果 usbipd 用不到，建议直接卸掉"
            }
        } catch {
            Warn "usbipd list 执行失败：$_"
        }
    } else {
        Ok "没有安装 usbipd（好，少一个坑）"
    }

    # 2) 幽灵设备
    Head "USB 存储设备枚举情况"
    $present = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
                 Where-Object { $_.InstanceId -like 'USBSTOR*' -or $_.InstanceId -like 'USB\VID*' -and $_.Class -eq 'DiskDrive' })
    if ($present.Count -gt 0) {
        Ok "当前在线的 USB 存储设备：$($present.Count) 个"
        $present | Select-Object Status, FriendlyName, InstanceId | Format-Table -AutoSize | Out-String | Write-Host
    } else {
        Warn "当前没有任何在线的 USB 存储设备"
        Info "读卡器插好了吗？卡插进读卡器了吗？"
    }

    $phantom = @(Get-PnpDevice -ErrorAction SilentlyContinue |
                 Where-Object { $_.Status -eq 'Unknown' -and $_.InstanceId -like 'USBSTOR*' })
    if ($phantom.Count -gt 3) {
        Warn "有 $($phantom.Count) 个幽灵（PHANTOM）USB 存储记录 —— 说明以前认过很多读卡器"
    } else {
        Ok "幽灵设备记录数量正常（$($phantom.Count)）"
    }

    Head "磁盘 Online/Offline 状态"
    Get-Disk | Select-Object Number, FriendlyName, SerialNumber, Size, BusType, PartitionStyle,
                              OperationalStatus, IsOffline, IsReadOnly |
        Format-Table -AutoSize | Out-String | Write-Host

    $offline = @(Get-Disk | Where-Object { $_.IsOffline -and $_.BusType -eq 'USB' })
    if ($offline.Count -gt 0) {
        Warn "有 USB 盘处于 Offline —— 这就是 Etcher 报 'writer process ended unexpectedly' 的原因"
        foreach ($d in $offline) {
            Info "修复： Set-Disk -Number $($d.Number) -IsOffline `$false"
        }
    } else {
        Ok "没有 USB 盘处于 Offline"
    }

    Head "判定速查"
    Write-Host @"
  症状                               -> 方向
  ---------------------------------  ------------------------------------------
  插卡后什么都没有（无枚举事件）      -> usbipd / 驱动栈拦截（跑本脚本 -Check）
  认到 USB 但没磁盘                   -> 卡槽接触 / 卡本身
  磁盘出现但 Offline                  -> Set-Disk -IsOffline 修复
  磁盘出现且 Online                   -> 正常，可以刷
  写入后回读不一致                    -> 卡 / 读卡器 / 板子 有硬件问题
"@
}

# ─────────────────────────────────────────────────────────────
# 列出物理盘
# ─────────────────────────────────────────────────────────────
function Invoke-List {
    Head "物理磁盘（人工核对：目标必须是 USB + 可移动 + 容量对得上）"
    Get-Disk | Select-Object Number, FriendlyName, SerialNumber, @{n='SizeGiB';e={[math]::Round($_.Size/1GB,1)}},
                              BusType, PartitionStyle, OperationalStatus, IsOffline, IsReadOnly |
        Sort-Object Number | Format-Table -AutoSize | Out-String | Write-Host

    Head "建议"
    Info "SD 卡应该是：BusType=USB，容量 16~128 GiB，PartitionStyle 通常 GPT"
    Info "NVMe / SATA 的盘一律不要动（那是你的系统盘）"
    Write-Host ""
    Info "如果 SD 卡是 Offline，先修："
    Write-Host "      Set-Disk -Number <卡号> -IsOffline `$false" -ForegroundColor White
}

# ─────────────────────────────────────────────────────────────
# 目标盘安全校验
# ─────────────────────────────────────────────────────────────
function Assert-SafeTarget ([int]$num) {
    $d = Get-Disk -Number $num -ErrorAction SilentlyContinue
    if (-not $d) { Die "找不到磁盘 $num" }

    Head "目标磁盘安全校验"

    # 1) 必须是 USB
    if ($d.BusType -ne 'USB') {
        Die "磁盘 $num 是 $($d.BusType)，不是 USB —— 拒绝写入（这几乎肯定是你的系统盘）"
    }
    Ok "总线类型 USB"

    # 2) 不能含 Windows 系统分区
    $sysVol = $env:SystemDrive
    $parts = @(Get-Partition -DiskNumber $num -ErrorAction SilentlyContinue)
    foreach ($p in $parts) {
        if ($p.DriveLetter -and "$($p.DriveLetter):" -eq $sysVol) {
            Die "磁盘 $num 承载着 Windows 系统盘 $sysVol —— 拒绝写入"
        }
    }
    Ok "不含 Windows 系统盘"

    # 3) 容量合理
    $gib = [math]::Round($d.Size / 1GB, 1)
    if ($gib -lt 4)    { Die "磁盘 $num 只有 $gib GiB，太小，不像 SD 卡" }
    if ($gib -gt 512)  { Warn "磁盘 $num 有 $gib GiB，比常见 SD 卡大很多，确认这不是移动硬盘" }
    Ok "容量 $gib GiB"

    # 4) 状态
    Write-Host ""
    $d | Select-Object Number, FriendlyName, SerialNumber, @{n='SizeGiB';e={[math]::Round($_.Size/1GB,1)}},
                       BusType, OperationalStatus, IsOffline, IsReadOnly |
        Format-List | Out-String | Write-Host

    return $d
}

# ─────────────────────────────────────────────────────────────
# 镜像准备：解压 + SHA256
# ─────────────────────────────────────────────────────────────
function Prepare-Image ([string]$img) {
    if (-not (Test-Path $img)) { Die "镜像不存在：$img" }

    Head "镜像准备"
    $item = Get-Item $img
    Info "输入：$($item.FullName)"
    Info "压缩包大小：$([math]::Round($item.Length/1MB,1)) MiB"

    $raw = $null
    switch -Regex ($img) {
        '\.xz$' {
            $raw = $img -replace '\.xz$', ''
            if (Test-Path $raw) {
                Ok "已有解压结果，复用：$raw"
            } else {
                Info "用 7-Zip 解压（约需 1~3 分钟）"
                $sevenZip = @(
                    "C:\Program Files\7-Zip\7z.exe",
                    "C:\Program Files (x86)\7-Zip\7z.exe",
                    "D:\APP\7-Zip\7z.exe"
                ) | Where-Object { Test-Path $_ } | Select-Object -First 1

                if (-not $sevenZip) {
                    Die "没找到 7z.exe，请先安装 7-Zip，或用其他工具先解压出 .img 再传入"
                }
                & $sevenZip x $img "-o$(Split-Path $img)" -y | Out-Null
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path $raw)) {
                    Die "解压失败（镜像可能损坏，或磁盘空间不足）"
                }
                Ok "解压完成：$raw"
            }
        }
        '\.img$' { $raw = $img; Ok "已是裸镜像" }
        default  { Die "不认识的格式：$img（支持 .img / .img.xz）" }
    }

    $rawItem = Get-Item $raw
    Info "裸镜像大小：$([math]::Round($rawItem.Length/1MB,1)) MiB"

    Head "计算镜像 SHA256（大文件约 1 分钟）"
    $h = Get-FileHash $raw -Algorithm SHA256
    Ok "SHA256: $($h.Hash)"
    Set-Content -Path "$raw.sha256" -Value "$($h.Hash)  $rawItem.Name" -Encoding ASCII
    Info "已写入 $raw.sha256"

    return $raw
}

# ─────────────────────────────────────────────────────────────
# 读取文件/设备前 N MiB 的 SHA256
#   （必须定义在 Invoke-Flash 之前：PowerShell 按出现顺序解析函数）
# ─────────────────────────────────────────────────────────────
function Get-RangeHash ([string]$path, [int]$mb) {
    $fs = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite, 4MB)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $buf = New-Object byte[] (1MB)
        $remaining = [int64]$mb * 1MB
        while ($remaining -gt 0) {
            $toRead = [int][math]::Min($buf.Length, $remaining)
            $n = $fs.Read($buf, 0, $toRead)
            if ($n -le 0) { break }
            $sha.TransformBlock($buf, 0, $n, $null, 0) | Out-Null
            $remaining -= $n
        }
        $sha.TransformFinalBlock(@(), 0, 0) | Out-Null
        return ($sha.Hash | ForEach-Object { $_.ToString('x2') }) -join ''
    } finally { $fs.Dispose() }
}

# ─────────────────────────────────────────────────────────────
# 刷写
# ─────────────────────────────────────────────────────────────
function Invoke-Flash ([string]$dev, [string]$rawImg, [int]$num) {
    $partitions = @(Get-Partition -DiskNumber $num -ErrorAction SilentlyContinue)

    Head "写前最终确认"
    Write-Host "  镜像：$rawImg" -ForegroundColor White
    Write-Host "  设备：\\.\PhysicalDrive$num" -ForegroundColor White
    Write-Host "  分区：$(if ($partitions.Count) { ($partitions | ForEach-Object { 'p' + $_.PartitionNumber }) -join ' ' } else { '无' })" -ForegroundColor White
    Write-Host ""

    if (-not $Yes) {
        Write-Host "  ⚠️  即将彻底覆盖该磁盘上的全部数据！" -ForegroundColor Red
        Write-Host "  输入大写的 YES 继续，其他任何输入都会取消： " -NoNewline
        $ans = Read-Host
        if ($ans -ne 'YES') { Die "用户取消。" }
    }

    # 保证可写、在线
    Set-Disk -Number $num -IsOffline $false -ErrorAction SilentlyContinue
    Set-Disk -Number $num -IsReadOnly $false -ErrorAction SilentlyContinue

    # 卸载现有卷
    Head "卸载现有卷"
    $any = $false
    foreach ($p in $partitions) {
        if ($p.DriveLetter) {
            try {
                Remove-PartitionAccessPath -DiskNumber $num -PartitionNumber $p.PartitionNumber `
                    -AccessPath "$($p.DriveLetter):" -ErrorAction Stop
                Ok "已卸载 $($p.DriveLetter):"
                $any = $true
            } catch { Warn "卸载 $($p.DriveLetter): 失败（可能被占用）" }
        }
    }
    if (-not $any) { Info "没有需要卸载的卷" }

    if ($DryRun) {
        Head "干跑结束"
        Ok "全部检查通过。去掉 -DryRun 就是真刷。"
        return
    }

    # ── 真刷：用 .NET FileStream 直写物理盘 ──
    Head "开始写入"
    Info "大镜像 3~10 分钟，中途不要拔卡"

    $src = [System.IO.File]::OpenRead($rawImg)
    try {
        $dst = New-Object System.IO.FileStream(
            "\\.\PhysicalDrive$num",
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None,
            4MB)
        try {
            $buf = New-Object byte[] (4MB)
            $total = $src.Length
            $done = 0L
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $lastPct = -1

            while (($read = $src.Read($buf, 0, $buf.Length)) -gt 0) {
                $dst.Write($buf, 0, $read)
                $done += $read

                $pct = [int](($done / $total) * 100)
                if ($pct -ne $lastPct -and ($pct % 5 -eq 0)) {
                    $mbps = [math]::Round(($done / 1MB) / [math]::Max($sw.Elapsed.TotalSeconds, 0.001), 1)
                    Write-Host ("`r  写入 {0,3}%  {1,7:N1} / {2,7:N1} MiB  ({3} MiB/s)" -f `
                        $pct, ($done/1MB), ($total/1MB), $mbps) -NoNewline
                    $lastPct = $pct
                }
            }
            $dst.Flush($true)
            Write-Host ""
            Ok "写入完成（$([math]::Round($sw.Elapsed.TotalSeconds,1)) 秒）"
        } finally { $dst.Dispose() }
    } finally { $src.Dispose() }

    # ── 写后回读校验 ──
    if ($SkipVerify) {
        Warn "-SkipVerify：跳过回读校验（不推荐）"
    } else {
        Head "回读校验（前 $VerifyMB MiB）"
        Info "这一步能抓住「坏卡/坏读卡器/坏板子」—— 写入看起来成功但数据其实是错的"

        $imgHash  = Get-RangeHash $rawImg $VerifyMB
        $diskHash = Get-RangeHash "\\.\PhysicalDrive$num" $VerifyMB

        Write-Host "  镜像：$imgHash"
        Write-Host "  设备：$diskHash"

        if ($imgHash -eq $diskHash) {
            Ok "回读一致 —— 写入可靠"
        } else {
            Fail "回读不一致！写入不可靠。"
            Warn "这正是「坏卡/坏读卡器/坏板子」的典型症状，别直接拿去启动。"
            Warn "换读卡器或换卡重试；每次都这样就是硬件问题。"
            Die "校验失败"
        }
    }

    Head "结论"
    Ok "镜像已写入 PhysicalDrive$num"

    Write-Host ""
    Write-Host "  下一步：" -ForegroundColor Cyan
    Write-Host "  1) 安全弹出：Dismount-DiskImage 或在托盘里「安全删除硬件」"
    Write-Host "  2) 把卡插回 A7A，上电"
    Write-Host "  3) 看蓝色状态灯：闪烁=正常启动中，熄灭=启动出错"
    Write-Host "  4) 等 60~90 秒，再扫局域网找 IP"
    Write-Host ""
    Write-Host "  首次启动会扩容 rootfs + 初始化，可能自动重启一次，属正常。" -ForegroundColor Gray
    Write-Host ""
}

# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Radxa A733 SD 卡刷写工具（Windows）" -ForegroundColor Cyan
Write-Host "安全设计：拒写系统盘 · 写前人工核对 · 写后回读校验" -ForegroundColor Gray
Write-Host "─────────────────────────────────────────────────────"

# -Check 不需要管理员，先跑
if ($Check) { Invoke-Check; exit 0 }

if ($List) { Invoke-List; exit 0 }

Assert-Admin

if (-not $Image) {
    if ($DiskNumber -lt 0) {
        Invoke-List
        Write-Host ""
        Warn "缺少参数。先看清目标盘，再执行："
        Write-Host "      .\scripts\flash-a7a.ps1 -Image <镜像路径> -DiskNumber <盘号> -DryRun" -ForegroundColor White
        exit 1
    }
}

$target = Assert-SafeTarget $DiskNumber
$rawImg = Prepare-Image $Image
Invoke-Flash "" $rawImg $DiskNumber

if ($Harden) {
    Head "防腐化配置（在板子上执行）"
    Write-Host @"
  卡刷好了，但「为什么会烂」这个问题必须在系统起来后立刻处理，
  否则新卡会用同样的方式坏掉。正确顺序：

  1) 首次启动后立刻 SSH 进去，先做只读保护：
       sudo sed -i 's/ rw / ro /' /boot/extlinux/extlinux.conf
     （把 append 行里的 rw 改成 ro，避免每次启动都在不可靠的写路径上写盘）

  2) 然后按 docs/troubleshooting/a7a-sd-card-corruption.md 的 §七 做
     DTB 层面的修复（禁用无人使用的 mmc@4022000 通道 / no-1-8-v）

  3) 验收：连续重启 3 次，每次开机后 fsck 的 block 计数不应该增长。
     参考 ROOT-CAUSE 报告里那张表（每次启动 +2049 blocks = 还在烂）。

  ⚠️ 全程不要碰 U-Boot（不 saveenv、不 mmc write、不改 bootargs 之外的东西）。
"@
}
