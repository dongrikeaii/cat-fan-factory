# 模板文件夹说明

每个子文件夹就是一个模板版本，至少包含：

```text
templates/版本名/
├─ cat_base.png
├─ paw_foreground.png
├─ paw_mask_debug.png   # 由程序生成，不需要手工制作
└─ template.json        # 可选，调整卡片位置与角度
```

要求：

- `cat_base.png` 与 `paw_foreground.png` 的像素尺寸必须完全一致。
- `paw_foreground.png` 必须是带透明通道的 RGBA PNG。
- 透明区域表示卡片可以显示；不透明猫爪会盖在卡片上。

添加新版本：

1. 复制 `classic-cat` 文件夹并改成新名字。
2. 替换其中的 `cat_base.png` 和 `paw_foreground.png`。
3. 双击根目录的 `04_生成模板蒙版.bat`。
4. 双击 `03_切换模板.bat`，输入模板序号。

`template.json` 可以覆盖全局默认参数：

- `card_width_ratio`：消息卡片宽度相对底图宽度。
- `angle_degrees`：卡片旋转角度。
- `center_x_ratio`、`center_y_ratio`：卡片中心位置。
- `shadow_blur`、`shadow_opacity`：阴影模糊与透明度。
- `shadow_offset_x`、`shadow_offset_y`：阴影偏移像素。
