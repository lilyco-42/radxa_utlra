# Radxa Cubie A7A 3D 打印外壳

免螺丝卡扣外壳，适配官方散热片，板卡尺寸 85×56mm。

| 文件 | 说明 | 面数 |
| --- | --- | --- |
| `a7a-case-top.stl` | 上壳（各向同性重网格化修复版，已通过嘉立创校验） | 36034 |
| `a7a-case-bottom.stl` | 下壳 | 4508 |

## 打印建议

- 材料：PETG / ABS / 树脂均可；X 树脂为 SLA 盲盒随机色。
- 壁厚大于 1.2mm，薄处不低于 0.8mm。
- 两件总体积约 33.7cm³，符合免费打样（≤70cm³）条件。

## 来源与修复

- 原始设计来自 MakerWorld 模型 2558376「Cubie A7A 无螺丝外壳」，遵循其许可，仅供个人自用，禁止转传 / 商用。
- 上壳原始网格存在常规工具检测不到的反向三角面，经 PyMeshLab `meshing_isotropic_explicit_remeshing`（iterations=3, adaptive=True）重新生成全部三角面后修复，watertight / winding 一致 / 单连通分量。
