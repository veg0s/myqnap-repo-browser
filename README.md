# MyQNAP Repo 软件浏览器

浏览 [myqnap.org](https://www.myqnap.org/) 第三方 QNAP 软件仓库的纯静态网页：按架构/机型筛选、看版本与更新时间、拿安装包直链。**非官方项目**，与 QNAP 官方无关。

在线地址：`https://<USER>.github.io/myqnap-repo-browser/`（把 `<USER>` 换成你的 GitHub 用户名）

[![snapshot](https://github.com/<USER>/myqnap-repo-browser/actions/workflows/snapshot.yml/badge.svg)](https://github.com/<USER>/myqnap-repo-browser/actions/workflows/snapshot.yml)

## 特性

- **按架构与机型筛选**：`x86_64 / arm_64 / arm-x41 / arm-x19 / arm-x31 / x86 32-bit`，以及具体机型（TS-X35、TS-X16 …）
- **下载会匹配你的选择**：没选架构/机型时，卡片上的下载按钮会弹「选择架构」层；选过之后整站下载都按该架构解析，不会再把 x86_64 包装到 ARM 机器上
- **详情页**：截图、清洗后的简介、按安装包去重的下载列表、19 个原始字段、platformID 与 signature
- **渐进加载**：4.4 MB 的仓库边走边解析边出卡片，慢网下也能很快看到内容；可随时「停止」
- **筛选偏好会被记住**：cookie（`mqrepo_sel_v1`，1 年）+ localStorage 双写，刷新/下次打开仍在
- **卡片 / 表格**两种视图，深浅色主题，移动端自适应（筛选为底部抽屉）
- **离线可用**：支持拖入本地 `repo.xml`，数据源不可达时也能浏览

## 本地运行

推荐起一个静态服务（快照走同源请求）：

```bash
cd myqnap-repo-browser
python -m http.server 8080
# 打开 http://localhost:8080
```

直接双击 `index.html`（`file://`）也能用，但有两处差异：内置快照 `data/snapshot.json` 会被浏览器拦掉（回退到直连源站），cookie 写不进去（筛选改由 localStorage 记住）。

## 数据从哪来 / 怎么更新

页面数据有两条来源，按优先级：

1. **站点快照** `data/snapshot.json` —— 随仓库发布，同源、可被 GitHub Pages 的 CDN 缓存，首访最快
2. **源站** `https://myqnap.org/repo.xml` —— 点右上角「刷新」时流式拉取，成功后会写入浏览器 IndexedDB（24 小时内再打开直接读缓存）

快照是构建产物，页面没有写权限，**刷新不会改快照**。要更新站内数据，跑一次脚本并提交：

```bash
python tools/build_snapshot.py --self-check     # 默认从源站拉，也可 --local repo.xml
git add data/snapshot.json && git commit -m "chore: 更新仓库快照" && git push
```

脚本输出里会打印条目数、JSON 体积与 gzip 体积。注意：**`items` 顺序必须与 XML 一致**（前端拿数组下标当 id），所以不要手工编辑快照、也不要按字段重排。

### 自动更新（GitHub Actions）

`.github/workflows/snapshot.yml` 每天自动跑一次，**不用手动维护**：

- 触发：定时 **UTC 19:00（北京时间次日 03:00）**，或在 Actions 页面手动 `Run workflow`
- 流程：抓取源站 → `--self-check --strict` 自检（条目数、链接后缀、重复项）→ 组装站点 → **整站覆盖部署**
- 失败即停：源站不可达会重试 3 次；自检不通过直接非零退出，**不会发布半截数据**，GitHub 会邮件通知你

**为什么不把快照提交进仓库**：旧版本的安装包链接本来就失效了，留历史没有意义；而快照有 2.7 MB，每天提交一次一年能把仓库撑到几百 MB。所以这里走「Actions 产物部署」——每次部署都是一次性产物，`_site/` 目录不进版本库，仓库历史里没有快照的版本堆积，也不需要 force-push。

仓库里那份 `data/snapshot.json` 只是**本地预览用的基线**，线上用的是每次部署时现生成的。想更新基线就在本地跑一次脚本再提交（可选）。

唯一提交进仓库的是 `data/snapshot.status.json`（几百字节，记录 `lastRun` / `changed`）。两个作用：页面用它显示「上次检查时间」，区分「数据多久没变」和「多久没查过」；同时让仓库每天有一次提交，避免 GitHub 在 60 天无活动后自动停用定时任务。

首次启用注意：仓库 Settings → Actions → General 里确认 Workflow permissions 允许读写（workflow 已声明 `permissions: contents: write`，若组织策略改成只读需要在这里放开）。

## 技术说明

- 单文件前端：`index.html` 内联全部 CSS/JS，零依赖、零构建
- 数据流：`fetch` → `TextDecoder` 流式解码 → 按 `</item>` 切片 → 分批 `DOMParser` → 增量归一化 → 逐批插卡；收尾再做一次全量构建用于排序归位
- 架构判定：优先看安装包文件名后缀（`_x86_64.qpkg` 等），无后缀的少数条目用「全库多数票」回填；`pidArch` 会随快照一起发布，流式加载时作为种子，避免中途架构抖动
- 缓存：IndexedDB 存归一化前的数据集（schema v1，与快照同构）→ localStorage 仅作容量兜底
- 下载：一律由真实 `<a target="_blank">` 触发，不用 `window.open`（会被弹窗策略拦），也不做 HEAD 探测（跨域拿不到真实状态码）

## 部署到 GitHub Pages

用 **GitHub Actions 部署**（每天自动覆盖发布，见上一节）：

1. 在 GitHub 新建**空仓库** `myqnap-repo-browser`（不要勾 README / .gitignore）
2. 本地推送：

   ```bash
   git remote add origin https://github.com/<USER>/myqnap-repo-browser.git
   git push -u origin main
   ```

3. 仓库 **Settings → Pages → Build and deployment**：Source 选 **`GitHub Actions`**（不是 Deploy from a branch）
4. 到 **Actions → snapshot → Run workflow** 手动跑一次；跑完就能访问 `https://<USER>.github.io/myqnap-repo-browser/`

> 想改成「分支直发」也可以：Settings 里 Source 选 `Deploy from a branch` → `main` / `(root)`，效果一样能看，但那样线上的快照就是仓库里那份基线，不会每天更新，而且 `data/snapshot.json` 每次改动都会进历史。定时任务里的 `configure-pages` / `upload-pages-artifact` / `deploy-pages` 三步需要删掉。

注意：Pages 对 HTML 的缓存是 `max-age=600`，**发版后最多约 10 分钟才全局生效**。验证新版本可以加个查询串绕过 CDN：`.../index.html?v=2`。

## 免责声明

- 本项目是非官方第三方工具，与 QNAP、myqnap.org 均无隶属关系
- 软件元数据与安装包版权归 myqnap.org 及各软件作者所有；本项目不中转、不托管任何安装包，下载链接直接指向源站
- 数据可能滞后（取决于快照生成时间），安装前请自行核对版本与机型

## License

MIT，见 [LICENSE](LICENSE)。
