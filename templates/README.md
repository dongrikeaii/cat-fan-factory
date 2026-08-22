# 模板文件夹说明

每个子文件夹就是一个模板版本，至少包含：

```text
templates/版本名/
├─ cat_base.png       # 或放 cat_base.jpg/jpeg/webp，04 会自动转换
├─ paw_foreground.png
├─ paw_mask_debug.png   # 由程序生成，不需要手工制作
└─ template.json        # 可选，调整卡片位置与角度
```

要求：

- `cat_base.png` 与 `paw_foreground.png` 的像素尺寸必须完全一致。如果只有 JPG，请命名为 `cat_base.jpg`，程序会校正手机拍照方向并生成 PNG。
- `paw_foreground.png` 必须是带透明通道的 RGBA PNG。
- 透明区域表示卡片可以显示；不透明猫爪会盖在卡片上。

添加新版本：

1. 在 `templates` 里新建一个空文件夹，文件夹名就是模板名。
2. 放入 `cat_base.png` 或 `cat_base.jpg`，再放入 `paw_foreground.png`。
3. 双击根目录的 `04_生成模板蒙版.bat`，输入该模板的序号；输入 `A` 可检查全部。
4. 程序会自动转换 JPG、检查尺寸与透明通道，并生成 `paw_mask_debug.png`。失败时会直接说明缺少哪个文件或哪项不符合。
5. 打开 `paw_mask_debug.png` 检查覆盖区域，再双击 `03_切换模板.bat` 切换。

`template.json` 可以覆盖全局默认参数：

- `card_width_ratio`：消息卡片宽度相对底图宽度。
- `angle_degrees`：卡片旋转角度。
- `center_x_ratio`、`center_y_ratio`：卡片中心位置。
- `top_y_ratio`：可选，将不同高度卡片的上边缘固定在底图同一高度；设置后会代替 `center_y_ratio` 的纵向定位。
- `shadow_blur`、`shadow_opacity`：阴影模糊与透明度。
- `shadow_offset_x`、`shadow_offset_y`：阴影偏移像素。
