# Cat Fan Factory

把“新关注我的”长截图自动切成单条关注卡片，并合成到猫咪怀里的 Windows 本地工具。

整个过程在电脑本地完成：截图、头像、昵称、OCR数据库和生成结果默认不会上传到网络，也已被 `.gitignore` 排除在 GitHub 仓库之外。

## 能做什么

- 根据右侧红色“回关”按钮自动识别一张截图中的多条记录。
- 离线OCR读取昵称和“关注了你”。
- 使用昵称、头像感知指纹和昵称区域指纹避免重复生成。
- 支持多个猫咪模板版本并一键切换。
- 自动从 `paw_foreground.png` 的透明度生成精确的 `paw_mask_debug.png`。
- 每次处理创建独立的时间批次文件夹，方便查看和整批清理。
- 低置信度、纯表情昵称、截图边缘残缺的记录进入人工复核目录。

## 一、最简单的安装方法

系统要求：Windows 10/11、64位 Python 3.11。

### 方法A：下载ZIP

1. 打开仓库页面：<https://github.com/dongrikeaii/cat-fan-factory>
2. 点击绿色 `Code` 按钮，再点击 `Download ZIP`。
3. 解压到普通文件夹，不要直接在压缩包内运行。
4. 如果电脑没有Python，从 <https://www.python.org/downloads/> 安装64位 Python 3.11，并勾选 `Add python.exe to PATH`。
5. 双击 `00_安装环境.bat`。
6. 等待依赖安装和测试全部完成。

`.venv` 不上传到GitHub，因为它体积大且不能可靠跨电脑复制；`00_安装环境.bat` 会在每台电脑上自动创建完整、隔离的运行环境。

### 方法B：让Codex或其他编程Agent安装

把下面这段话完整发给Agent：

```text
请在我的Windows电脑上安装并验证 Cat Fan Factory：
1. 克隆 https://github.com/dongrikeaii/cat-fan-factory.git
2. 进入仓库根目录。
3. 运行 cmd /c 00_安装环境.bat。
4. 再运行 .\.venv\Scripts\python.exe -m unittest discover -s tests -v。
5. 告诉我项目绝对路径、测试结果和 inbox 文件夹位置。
不要上传或读取我项目外的私人截图，也不要提交 inbox、output、data 或 .venv。
```

Agent也可以直接执行：

```powershell
git clone https://github.com/dongrikeaii/cat-fan-factory.git
Set-Location -LiteralPath '.\cat-fan-factory'
cmd /c 00_安装环境.bat
```

## 二、日常处理截图

1. 在手机上手动截取“新关注我的”完整列表。
2. 尽量让截图第一条和最后一条都完整显示。
3. 把 PNG/JPG 截图复制到 `inbox`。
4. 双击 `01_处理一次.bat`。
5. 打开 `output/batches` 中最新的时间文件夹。

每次运行会生成类似结构：

```text
output/batches/2026-08-21_21-30-15/
├─ final/               正式成品
├─ cropped_rows/        自动切出的单条关注卡片
├─ needs_review/        需要人工确认的成品和JSON原因
├─ source_screenshots/  本批次处理过的原截图
└─ report.json          本批次处理报告
```

确定某一批不再需要时，可以直接删除整个时间文件夹。全局去重历史保存在 `data/processed.sqlite3`，删除批次文件夹不会自动清空去重记录。

持续监听模式：双击 `02_持续监听.bat`，保持黑色窗口打开。新截图放入 `inbox` 后会自动处理；按 `Ctrl+C` 停止。

## 三、增加新模板

每个模板都是 `templates` 下的一个文件夹：

```text
templates/my-cat-v2/
├─ cat_base.png
├─ paw_foreground.png
├─ paw_mask_debug.png   自动生成
└─ template.json        可选
```

操作步骤：

1. 新建 `templates/版本名` 文件夹。
2. 放入尺寸完全相同的 `cat_base.png` 和 `paw_foreground.png`。
3. `paw_foreground.png` 必须有透明背景。
4. 双击 `04_生成模板蒙版.bat`。
5. 检查生成的黑白 `paw_mask_debug.png`。
6. 双击 `03_切换模板.bat`，输入模板序号。
7. 双击 `05_查看状态.bat` 确认当前模板。

需要修改卡片角度和位置时，复制 `classic-cat/template.json` 到新模板文件夹后调整数值。详细字段见 [templates/README.md](templates/README.md)。

## 四、按钮说明

| 文件 | 用途 |
|---|---|
| `00_安装环境.bat` | 首次安装依赖、生成蒙版并运行测试 |
| `01_处理一次.bat` | 处理当前 `inbox`，生成时间批次 |
| `02_持续监听.bat` | 持续监听 `inbox` |
| `03_切换模板.bat` | 交互选择当前模板版本 |
| `04_生成模板蒙版.bat` | 为所有完整模板重新生成Alpha蒙版 |
| `05_查看状态.bat` | 查看模板、批次和去重统计 |

## 五、命令行与开发验证

```powershell
.\.venv\Scripts\python.exe app.py prepare-templates
.\.venv\Scripts\python.exe app.py list-templates
.\.venv\Scripts\python.exe app.py set-template classic-cat
.\.venv\Scripts\python.exe app.py process
.\.venv\Scripts\python.exe app.py status
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 隐私与使用边界

- 不要把真实关注截图、头像、昵称、OCR数据库或生成成品提交到GitHub。
- 提交前检查 `git status --short --ignored`。
- 本工具只处理用户主动放入 `inbox` 的本地截图，不自动登录、抓取或控制社交平台。
- 如果公开发布带有粉丝头像和昵称的成品，请自行确认隐私和平台规则。

## License

[MIT](LICENSE)
