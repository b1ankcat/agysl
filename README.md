# agysl — Zero-Dependency Antigravity CLI (`agy`) StatusLine

纯 Python 3 标准库实现的 Antigravity CLI 扩展状态栏（**零第三方依赖**），固定堆叠在默认状态栏下方（`stack_with_default: true`），以 `│` 分割四个独立高对比度彩色区域（避开黑 / 紫 / 绿等常见背景色），并根据终端宽度自动在单行内平均分布：

- **Context 进度条（亮青色）**：`ctx ████░░░░ 42% (420.0k/1.00M)`
- **Usage 5h / week 进度条（亮琥珀黄）**：`usage 5h/wk ███░/█░░░ 72%(2h)/22%(92h)`（显示剩余百分比及距离刷新时间）
- **Cache Hit 进度条（亮橙色）**：`cache ███████░ 90%`（优先读取 `context_window.current_usage`，缺失时自动回退读取当前会话 `transcript.jsonl`）
- **每日 Token 统计（亮天蓝）**：`day token 12.3m(2.35$)`（汇总当天所有会话的输入+缓存+输出 token 及按最新模型公开定价计算的预估费用，token 单位为 `m`，达到 10 亿后自动切换为 `b`）

## 目录结构

- `agysl.py`：主渲染脚本（读取 `agy` 传入 `stdin` 的状态 JSON，输出 ANSI 彩色状态栏）
- `install.sh`：将 `agysl.py` 复制/更新到 `~/.gemini/antigravity-cli/agysl.py`，并将 `statusLine` 安全写入 `~/.gemini/antigravity-cli/settings.json`（固定 `stack_with_default: true`，自动备份原配置为 `.bak`），脱离原项目目录也可独立运行
- `test_agysl.py`：单元测试

## 安装与启用

```bash
cd /root/project/agysl
chmod +x agysl.py install.sh
./install.sh
```

卸载：

```bash
./install.sh --uninstall
```

> 或手动在 `~/.gemini/antigravity-cli/settings.json` 中添加：
>
> ```json
> {
>   "statusLine": {
>     "type": "command",
>     "command": "python3 ~/.gemini/antigravity-cli/agysl.py",
>     "enabled": true,
>     "padding": 0,
>     "stack_with_default": true
>   }
> }
> ```

## 环境变量配置

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `AGYSL_SHOW_TOKENS` | `0` | 设为 `1` 追加显示累计 token（`in+cache/out`） |
| `AGYSL_MULTILINE` | `0` | 设为 `1` 拆分为两行显示（适合窄终端） |
| `AGYSL_NO_COLOR` / `NO_COLOR` | `0` | 设为 `1` 禁用 ANSI 颜色 |

## 本地测试

```bash
python3 -m unittest -v /root/project/agysl/test_agysl.py
```

也可用模拟 JSON 直接预览效果：

```bash
printf '%s' '{"cycle_mode":"auto","model":{"id":"gemini-4-hydrogen-high","display_name":"Gemini 4 Hydrogen (High)","effort":"high"},"context_window":{"context_window_size":1000000,"used_percentage":36,"current_usage":{"input_tokens":2800,"cache_read_input_tokens":34200}},"quota":{"gemini-5h":{"remaining_fraction":0.82},"gemini-weekly":{"remaining_fraction":0.65}}}' | python3 /root/project/agysl/agysl.py
```
