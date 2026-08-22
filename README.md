# Cat Fan Factory

把“新关注我的”长截图自动切成单条关注卡片，并合成到猫咪怀里的 Windows 本地工具。

同时支持“粉丝指数 > 关注我的人”粉丝列表截图。程序只识别每行顶部昵称，不会把下方个签或状态当成昵称。

截图处理、OCR和合成默认在电脑本地完成；只有你主动运行飞书同步命令时，正式成品、昵称和查询信息才会上传到你配置的飞书多维表格。截图、OCR数据库、生成结果、表格标识和同步状态均已被 `.gitignore` 排除在 GitHub 仓库之外。

## 能做什么

- 根据右侧红色“回关”按钮自动识别一张截图中的多条记录。
- 离线OCR读取昵称和“关注了你”。
- 使用昵称、头像感知指纹和昵称区域指纹避免重复生成。
- 支持多个猫咪模板版本并一键切换。
- 自动从 `paw_foreground.png` 的透明度生成精确的 `paw_mask_debug.png`。
- 每次处理创建独立的时间批次文件夹，方便查看和整批清理。
- 低置信度、纯表情昵称、截图边缘残缺的记录进入人工复核目录。
- 可选择把正式成品同步到飞书多维表格，支持跨电脑远端去重。

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

1. 在手机上手动截取“新关注我的”，或“粉丝指数 > 关注我的人”完整列表。
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

仓库内置 `classic-cat` 和 `Orange Cat` 两个示例模板，可双击 `03_切换模板.bat` 切换。

操作步骤：

1. 新建 `templates/版本名` 文件夹。
2. 放入尺寸完全相同的 `cat_base.png` 和 `paw_foreground.png`。
3. `paw_foreground.png` 必须有透明背景。
4. 双击 `04_生成模板蒙版.bat`。
5. 检查生成的黑白 `paw_mask_debug.png`。
6. 双击 `03_切换模板.bat`，输入模板序号。
7. 双击 `05_查看状态.bat` 确认当前模板。

需要修改卡片角度和位置时，复制 `classic-cat/template.json` 到新模板文件夹后调整数值。详细字段见 [templates/README.md](templates/README.md)。

## 四、同步到飞书多维表格（可选）

### 首次配置

1. 创建飞书企业自建应用并开启机器人能力。
2. 为应用申请“查看、评论、编辑和管理多维表格”及上传素材所需权限，发布应用。
3. 在多维表格右上角点击 `···` > `…更多` > `添加文档应用`，选择该应用并授予可管理权限。
4. 在 Windows 用户环境变量中设置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，设置后重新打开终端或重启 Agent。
5. 双击 `06_配置飞书.bat`，粘贴普通多维表格网址。程序会检查凭证、权限，并补齐“成品图片、生成时间、查询码、模板版本、生成批次、去重键、上传状态”字段。

真实 `App Secret` 只能存放在每台电脑自己的环境变量中，不能写入项目文件、聊天记录或 GitHub。表格链接解析出的标识只保存在 `data/feishu_config.json`，该文件不会提交。

### 日常同步

1. 正常运行 `01_处理一次.bat` 并检查最新批次的 `final` 文件夹。
2. 确认图片和昵称无误后，双击 `07_同步飞书.bat`。
3. 程序仅上传数据库中未标记为待复核、且本地成品仍存在的记录。
4. 程序把稳定的去重键写入飞书；另一台电脑同步时会先读取远端去重键，避免重复上传。
5. 上传失败会保留本地图片和同步状态，再次双击即可重试。

需要一次放入多张截图时，可以把它们全部复制到 `inbox`，再双击 `08_批量处理并上传飞书.bat`。程序会把这些截图放进同一个新批次，先显示上传预览；只有输入大写 `UPLOAD` 后，才会上传本次新批次。历史批次、待复核结果和纯符号占位昵称不会被上传。

第一次接入时，Agent可以运行下面的命令创建一条不含粉丝信息的连接测试记录：

```powershell
.\.venv\Scripts\python.exe app.py test-feishu
```

测试记录确认成功后可以在飞书中手动删除。对粉丝分享时，不要直接分享工作表地址；请创建只显示“昵称、成品图片、生成时间、查询码”的独立视图，再设置为互联网获得链接的人可阅读。

## 五、按钮说明

| 文件 | 用途 |
|---|---|
| `00_安装环境.bat` | 首次安装依赖、生成蒙版并运行测试 |
| `01_处理一次.bat` | 处理当前 `inbox`，生成时间批次 |
| `02_持续监听.bat` | 持续监听 `inbox` |
| `03_切换模板.bat` | 交互选择当前模板版本 |
| `04_生成模板蒙版.bat` | 为所有完整模板重新生成Alpha蒙版 |
| `05_查看状态.bat` | 查看模板、批次和去重统计 |
| `06_配置飞书.bat` | 保存目标表格、检查权限并补齐字段 |
| `07_同步飞书.bat` | 上传正式成品并执行跨电脑远端去重 |
| `08_批量处理并上传飞书.bat` | 处理 inbox 全部截图，确认后只上传本次新批次 |

## 六、命令行与开发验证

```powershell
.\.venv\Scripts\python.exe app.py prepare-templates
.\.venv\Scripts\python.exe app.py list-templates
.\.venv\Scripts\python.exe app.py set-template classic-cat
.\.venv\Scripts\python.exe app.py process
.\.venv\Scripts\python.exe app.py status
.\.venv\Scripts\python.exe app.py configure-feishu --url "https://example.feishu.cn/base/BASE_TOKEN?table=TABLE_ID"
.\.venv\Scripts\python.exe app.py test-feishu
.\.venv\Scripts\python.exe app.py sync-feishu
.\.venv\Scripts\python.exe app.py sync-feishu --latest-batches 2 --dry-run
.\.venv\Scripts\python.exe app.py sync-feishu --latest-batches 2
.\.venv\Scripts\python.exe app.py process-and-sync
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 隐私与使用边界

- 不要把真实关注截图、头像、昵称、OCR数据库或生成成品提交到GitHub。
- 提交前检查 `git status --short --ignored`。
- 本工具只处理用户主动放入 `inbox` 的本地截图，不自动登录、抓取或控制社交平台。
- 飞书同步必须由用户主动运行，不会后台自动上传；待复核图片不会自动上传。
- 如果公开发布带有粉丝头像和昵称的成品，请自行确认隐私和平台规则。

## License

[MIT](LICENSE)
