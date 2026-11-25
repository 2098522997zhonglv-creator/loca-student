---
name: playwright-skill
description: 浏览器自动化执行工具。用于执行 Web 页面测试、表单填写、登录验证、截图等浏览器操作。
---

# Playwright 浏览器自动化

## 连续执行与耗时

对已给定 URL、颜色、文本和数量的购物车测试，先以 `knowledge_search` 查询必要规则并判断适用性，再调用内置表单操作：
1. `execute_skill_script(skill_name="playwright-skill", browser_plan={"action":"inspect_form","url":"目标 PDP","screenshots":true})`：读取真实 DOM 并返回 `bindings`，无需重复读取 Skill。
2. 同一会话调用 `browser_plan={"action":"run_form_test","url":"同一 PDP","bindings":上一步绑定,"rows":[{"key":"row-1","value":"文本","color":"完整颜色名","quantity":1}],"empty_check":true,"no_extras":true,"screenshots":true}`：连续执行必填、预览、参数行与购物车回读。参数行必须对应当前任务，不包含已执行行；额外前置操作先在原脚本路径完成。
3. `needs_binding` 表示未知控件，按 snapshot 修复当前步骤；已确认的成功和业务失败都保留，未知写入先回查。此操作不能覆盖上传、登录、下单等额外目标。

等待页面条件、实际响应、预览更新；不要插入固定秒数等待。使用 Runtime 的 page/context 与原生 script，读取本机产物使用 Node fs，避免跨系统 shell 命令。内置操作已经读取页面结构，原始脚本仍先检查当前 DOM。


执行浏览器自动化任务，支持页面测试、表单操作、登录验证等。**Runtime 默认主浏览器**；`playwright-cli` / `browser-use` 仅作二层等价备用（切换前探测可用性并尽量共享会话）。

## 业务断言与证据（复杂用例必读）

| 检查对象 | 合格证据 | 不能作为通过依据 |
|---|---|---|
| 登录 | 当前用户信息或受保护资源验证 | 填过表单、存在密码框、`login_ok=true` 硬编码 |
| 上传/预览 | 上传返回与文件关联 + 预览实际更新 | 点过上传、源码含 preview、占位图/旧图 |
| 购物车 | 加购响应及购物车回读（商品/数量/金额） | 仅点击 Add To Cart、空车、别的商品 |
| 订单 | 本次创建的真实订单标识并回读状态 | URL 含 order、知识库示例号、`cart_id` 冒充 `order_sn` |

动作与预期必须拆开节点（如「上传」+「预览断言」）。工具 `succeeded` ≠ 业务通过。结果未知时先回查，不重放加购/下单。

## ⚠️ 强制规则：先获取页面结构，再操作元素

**禁止猜测选择器！禁止通过截图识别元素！**

打开任何页面后，必须**立即**调用 `helpers.describePageForAI(page)` 获取页面元素列表，然后根据返回的选择器进行操作。

```javascript
// 打开页面后第一件事：获取页面结构
await page.goto('http://example.com');
const desc = await helpers.describePageForAI(page);
console.log(desc);  // 输出所有可交互元素及其选择器
```

输出示例：
```
## Page: loca_stude
URL: http://192.168.150.114:8913/login

### Input Fields (2)
- text: selector="#username" placeholder="请输入用户名"
- password: selector="#password" placeholder="请输入密码"

### Buttons (1)
- "登录": selector="button[type="submit"]"
```

然后根据输出的选择器进行操作：
```javascript
await page.fill('#username', 'admin');
await page.fill('#password', '123456');
await page.click('button[type="submit"]');
```

## 使用方法

优先通过 `execute_skill_script(skill_name="playwright-skill", script="原始 JavaScript", session_id="本任务会话")` 调用。`script` 支持真实换行、引号和正则，不需要 shell 转义；非空 `script` 优先于兼容字段 `command`，空 `script` 按未提供处理。已确认的页面结构和同一隔离游客会话在本任务内复用。

兼容旧调用时可传 `command`：

```
node run.js "your playwright code here"
```

`command` 经 shell 传输时需要正确转义；优先使用 `script` 避免 Windows 引号问题。不要添加 --session、--inline、--eval 参数。

## 参数化测试的推进与收敛

- 首次读取页面结构后，用稳定定位器连续完成同类参数行；可在一次 `script` 内用循环、逐行 try/catch 和 finally 截图，减少模型往返。保留每行参数、预览、响应与证据。
- 用 `page.waitForResponse` 观察实际点击触发的请求。HTTP 400 / FAILED_PRECONDITION 等明确业务拒绝记为该行失败，继续剩余参数行；不要对同一错误反复诊断或自行切换对照商品。超时或状态未知先回读，避免重放可能已成功的写入。
- 所有行结束后回读本会话购物车，核验商品、颜色、文本、数量、金额及真实标识。失败行没有生成标识时如实写明。交付中文逐项报告与截图；工具正常退出不等于用例通过。
- 使用 Runtime 已提供的 `browser/context/page`，无需重复启动浏览器。保持会话到核验结束；仅当任务明确要求重建会话时替换。

## 截图路径约定

**必须使用环境变量 `process.env.SCREENSHOT_DIR`** 作为截图保存目录。系统会自动设置该变量指向 playwright-skill 目录下的 `media/screenshots/` 子目录。

截图命名建议：`case_{case_id}_step{step_number}.png`

**示例**：
```javascript
const screenshotDir = process.env.SCREENSHOT_DIR || './media/screenshots';
await page.screenshot({ path: `${screenshotDir}/case_11_step1.png` });
```

## 基础示例

### 打开页面并截图

```bash
node run.js "const dir = process.env.SCREENSHOT_DIR; const browser = await chromium.launch({ headless: false }); const page = await browser.newPage(); await page.goto('http://example.com'); await page.screenshot({ path: dir + '/example.png' }); console.log('截图已保存:', dir + '/example.png'); await browser.close();"
```

### 登录测试（带截图）

```bash
node run.js "const dir = process.env.SCREENSHOT_DIR; const browser = await chromium.launch({ headless: false, slowMo: 100 }); const page = await browser.newPage(); await page.goto('http://192.168.150.114:8913/'); await page.screenshot({ path: dir + '/step1_open.png' }); await page.fill('input[type=\"text\"]', 'admin'); await page.fill('input[type=\"password\"]', 'admin123456'); await page.screenshot({ path: dir + '/step2_filled.png' }); await page.click('button[type=\"submit\"]'); await page.waitForTimeout(2000); await page.screenshot({ path: dir + '/step3_result.png' }); console.log('截图已保存到', dir); await browser.close();"
```

### 表单填写

```bash
node run.js "const browser = await chromium.launch({ headless: false }); const page = await browser.newPage(); await page.goto('http://example.com/form'); await page.fill('input[name=\"username\"]', 'testuser'); await page.fill('input[name=\"email\"]', 'test@example.com'); await page.click('button[type=\"submit\"]'); console.log('表单提交完成'); await browser.close();"
```

## 其他 helpers 函数

### helpers.getPageStructure(page) - 获取结构化 JSON

返回 JSON 对象，适合程序化处理。

```javascript
const structure = await helpers.getPageStructure(page);
console.log(JSON.stringify(structure, null, 2));
```

### helpers.getPageText(page) - 获取纯文本内容

返回页面所有可见文本。

```javascript
const text = await helpers.getPageText(page);
console.log(text);
```

## 常用 API

### 浏览器启动

```javascript
// 可见模式（推荐调试）
const browser = await chromium.launch({ headless: false, slowMo: 100 });

// 无头模式（后台执行）
const browser = await chromium.launch({ headless: true });
```

### 页面导航

```javascript
await page.goto('http://example.com');
await page.goto('http://example.com', { waitUntil: 'networkidle' });
```

### 元素定位与操作

```javascript
// 输入文本
await page.fill('input[name="username"]', 'admin');
await page.fill('input[type="password"]', '123456');

// 点击
await page.click('button[type="submit"]');
await page.click('text=登录');
await page.click('.login-btn');

// 等待元素
await page.waitForSelector('.success-message');
await page.waitForURL('**/dashboard');
```

### 常用选择器

| 选择器类型 | 示例 |
|-----------|------|
| CSS | `input[name="username"]`, `.login-btn`, `#submit` |
| 文本 | `text=登录`, `button:has-text("提交")` |
| 类型 | `input[type="text"]`, `input[type="password"]` |
| 占位符 | `input[placeholder*="账号"]`, `input[placeholder*="密码"]` |

### 截图（使用环境变量）

```javascript
const dir = process.env.SCREENSHOT_DIR;
await page.screenshot({ path: `${dir}/screenshot.png` });
await page.screenshot({ path: `${dir}/full.png`, fullPage: true });
```

### 等待

```javascript
await page.waitForTimeout(2000);  // 等待2秒
await page.waitForLoadState('networkidle');  // 等待网络空闲
```

## 完整测试流程示例

执行一个完整的登录测试：

```bash
node run.js "const dir = process.env.SCREENSHOT_DIR; const browser = await chromium.launch({ headless: false, slowMo: 100 }); const page = await browser.newPage(); console.log('步骤1: 打开登录页'); await page.goto('http://192.168.150.114:8913/'); await page.screenshot({ path: dir + '/step1_open.png' }); console.log('步骤2: 输入账号'); await page.fill('input[type=\"text\"]', 'admin'); console.log('步骤3: 输入密码'); await page.fill('input[type=\"password\"]', 'admin123456'); await page.screenshot({ path: dir + '/step2_input.png' }); console.log('步骤4: 点击登录'); await page.click('button[type=\"submit\"]'); await page.waitForTimeout(2000); await page.screenshot({ path: dir + '/step3_result.png' }); console.log('登录结果 - 当前URL:', page.url()); await browser.close(); console.log('测试完成，截图保存在:', dir);"
```

## 注意事项

1. **截图路径**：必须使用 `process.env.SCREENSHOT_DIR` 环境变量
2. **代码格式**：inline 代码用分号分隔语句，写在一行内
3. **引号转义**：字符串内的双引号需要转义 `\"`
4. **browser.close()**：非持久化模式下执行完毕后务必关闭浏览器
5. **console.log()**：用于输出执行进度和结果
6. **headless: false**：调试时使用可见模式，方便观察

## 持久化会话模式

对于需要**跨多个步骤保持浏览器打开**的场景（如自动化测试用例），使用 `session_id` 参数：

### ⚠️ 核心规则（必须严格遵守）

1. **session_id 必须完全一致**：整个测试流程中所有步骤**必须使用完全相同的 session_id 字符串**，否则会创建新浏览器导致状态丢失！
   - ✅ 正确：所有步骤都用 `session_id="case_11"`
   - ❌ 错误：第一步用 `case_11`，第二步用 `case-11`，第三步用 `test-case-11`（这会创建3个不同的浏览器！）
2. **直接使用 `page` 变量**：无需 `chromium.launch()`，系统自动管理
3. **不要调用 `browser.close()`**：浏览器由系统管理，空闲 15 分钟自动关闭

### 持久化模式示例

**步骤 1：打开页面**
```python
execute_skill_script(
    skill_name="playwright-skill",
    command='node run.js "await page.goto(\'http://example.com\'); console.log(page.url());"',
    session_id="test-case-001"
)
```

**步骤 2：填写表单（复用同一浏览器）**
```python
execute_skill_script(
    skill_name="playwright-skill",
    command='node run.js "await page.fill(\'input[name=username]\', \'admin\'); await page.fill(\'input[name=password]\', \'123456\');"',
    session_id="test-case-001"
)
```

**步骤 3：点击登录（继续复用）**
```python
execute_skill_script(
    skill_name="playwright-skill",
    command='node run.js "await page.click(\'button[type=submit]\'); await page.waitForTimeout(2000); console.log(\'登录后URL:\', page.url());"',
    session_id="test-case-001"
)
```

### 持久化 vs 非持久化对比

| 特性 | 非持久化（无 session_id） | 持久化（有 session_id） |
|------|--------------------------|------------------------|
| 浏览器生命周期 | 代码手动管理 | 系统自动管理 |
| 启动方式 | `chromium.launch()` | 直接使用 `page` |
| 关闭方式 | `browser.close()` | 自动关闭（15分钟空闲） |
| 跨步骤状态 | 不保持 | 保持（登录态、cookie等） |
| 适用场景 | 单步操作 | 多步骤测试用例 |
