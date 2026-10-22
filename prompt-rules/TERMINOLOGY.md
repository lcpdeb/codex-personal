# 术语表

| 术语 | 具体含义 |
|---|---|
| **plan** | 一份完整治疗计划，包含多个 beam 及其计算、优化设置。 |
| **beam** | 计划中的一个照射单元，具有能量、照射方向、MLC 配置等信息；可以采用固定方向，也可以覆盖一段 arc。 |
| **BEV** | Beam's Eye View，即从 source 沿 beam 方向观察的视角。 |
| **control point / CP** | 描述照射过程中某个位置的机器状态，例如 gantry angle、MLC leaf positions、jaw positions，以及累计照射进度。 |
| **segment** | 描述两个 control point 之间的一段照射过程，包含开始、结束状态以及这段的 MU。静态 segment 的形状保持不变；动态 segment 可以包含 leaf 或角度变化。 |
| **ray / beamlet** | 将 beam 的照射范围划分后得到的一个小面积计算单元。优化器可以调整各 beamlet 的权重，改变空间上的照射强度分布。 |
| **fluence map** | 描述 beam 不同位置上的照射强度分布。在当前 Hyperion 的 beamlet 优化表示中，由各 beamlet 的权重组成，相关接口以 MU 表示。 |
| **MU** | 表示机器照射输出量的计量单位；剂量还取决于机器、照射形状和患者情况。 |
