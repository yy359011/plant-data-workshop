# Windows 源码开发启动

这份源码可以直接运行 Python API 和 Vite 前端。编辑 `src/` 后浏览器热更新，无需 Nginx，也不依赖预先生成的 `dist/`。后端 Python 文件修改后，停止启动窗口并重新启动即可生效。

## 第一次启动

1. 将完整 ZIP 解压到可写目录，例如 `D:\植物研究所\数据工坊`。请先解压，不要在压缩软件里直接运行。中文、空格目录受支持。
2. 电脑需已有 **Python 3.10+** 和 **Node.js 22.13.0+（含 npm）**。安装时让命令可通过 PATH 找到，安装后重新打开启动窗口。版本门槛来自项目锁定的 `pdfjs-dist 6.4.299`；其要求高于 Vite 本身。启动器不会下载或安装 Python、Node.js。
3. 双击根目录 **`start-dev.cmd`**。脚本先检测环境与端口，再创建项目内 `.venv/`，用 pip 安装 `requirements-optional.txt` 中的 PDF / Excel 解析组件，用 `npm ci` 安装锁定的前端依赖，并准备本地 PDF 字体和渲染资源。
4. 首次安装需要能访问配置的 PyPI / npm 仓库；终端会显示安装进度。失败会保留错误提示，修复网络或安装问题后再次双击即可。后续启动按依赖文件和运行时签名复用依赖，文件变化才重新安装。
5. 就绪后自动打开 **http://127.0.0.1:5173**。先在服务器电脑的这个本机地址创建管理员，再由管理员创建同事账号。没有默认账号或密码，角色为管理员、内部人员、外部人员三个内置等级。

双击脚本默认传入 `--lan`，Vite 监听 `0.0.0.0:5173`；启动窗口会列出本机 IPv4 地址。同局域网同事访问 **`http://服务器IPv4:5173`** 登录。若 Windows 弹出防火墙授权，请只允许专用网络。不能从同事电脑或服务器的 LAN 地址执行第一次管理员创建。

Python API 始终只监听 **127.0.0.1:8766**。前端通过 Vite 代理 `/api`，转发真实访问者地址、原始 Host 和 Origin；后端只接受启动器登记的本机入口和 LAN IP 入口。仅需让同事访问前端端口，不用开放 API 端口。换网络/IP 后重新启动以更新允许的地址。此入口按 HTTP 配置；不要把它作为互联网公开服务。

## 停止与数据

在启动窗口按 **Ctrl-C** 会同时停止 Python 和 Vite。Windows 启动器使用作业对象管理子进程；窗口退出也会终止其管理的服务。若 `cmd` 询问是否终止批处理，选择 `Y`。一项服务启动失败或意外退出时，启动器会停止另一项，避免只留下半套服务。

资料、账号和配置持久化在项目 `data/`（或指定的 `--data-dir` / `PLANT_DATA_DIR`）。停止服务不会删除资料。备份或迁移时请先停止服务，再复制整个数据目录。交付包不带 `node_modules/`、`.venv/`、个人账号、会话及保存的 API 密钥；随包提供的文献数据与交付方工作数据隔离。OCR 和模型接口需管理员自行配置，未配置不会冒充已执行。

## 常用命令

在源码根目录打开命令提示符或 PowerShell：

```bat
rem 离线只读检查：不联网安装、不启动服务、不修改数据
start-dev.cmd --check

rem 仅本机访问（覆盖脚本默认 --lan）
start-dev.cmd --local

rem 使用不同端口及单独数据目录
start-dev.cmd --frontend-port 5183 --backend-port 8776 --data-dir "D:\植物资料\工坊数据"

rem 不自动打开浏览器
start-dev.cmd --no-browser
```

`--check` 成功表示源码与系统运行时具备启动条件。它会分别报告 Python / 前端依赖是否已有安装签名；“首次启动时安装”意味着正常启动仍需安装，不代表离线环境已经能运行全部功能。此检查不占用监听端口，不会验证 Windows 防火墙或远程电脑连通性。

端口被占用时脚本明确显示地址并退出，不会杀掉其他程序，也不会自动换到一个未知端口。关闭既有实例，或同时指定不同的前端、API 端口。前后端端口必须不同。

## 开发者验收

已有完整依赖时，可使用下面的显式选项跳过安装。没有 `.venv/` 时使用当前 Python，仍会检查 `pypdf`、`openpyxl`，并重新准备本地 PDF 静态资源：

```bat
py -3 scripts\dev.py --lan --skip-install --no-browser --frontend-port 5183 --backend-port 8776 --data-dir "D:\临时验收数据"
```

`--skip-install` 不是默认行为；缺依赖会报错，不会悄悄降级。也可以在已将 Python 加入 PATH 的环境使用 `npm run start:dev` 或 `npm run check:dev`。Windows 双击入口优先使用 `py -3`，没有 Python Launcher 时才尝试 `python`。

单独的 `npm run dev` 仍是前端开发命令，需要自行启动并正确登记代理 Origin 的后端；日常使用完整启动入口更方便。正常流程会自动运行与 `predev` 相同的 `scripts/prepare-pdf-assets.mjs`，PDF 渲染资源在本地提供。

验证命令：

```bat
py -3 -m unittest discover -s tests -p test_dev_launcher.py -v
npm run test:dev
```

本次交付在 macOS 上完成源码启动、代理及进程清理测试，并对 Windows 批处理和作业对象调用做静态检查。**尚未在 Windows 实机运行**；首次在同事电脑启动时仍需确认 Python/Node PATH、依赖安装和 Windows 防火墙。
