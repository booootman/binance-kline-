# Binance Futures Dashboard

本项目是一个本地 Binance U 本位合约行情分析仪表盘：Python 后端负责拉取行情、计算指标、输出策略快照；前端页面负责展示多周期建议、实时价格、风险提示和信号历史。

## 快速启动

```powershell
cd C:\code\bian
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

打开：

```text
http://127.0.0.1:8000/binance-futures-dashboard.html
```

命令行分析器仍然兼容旧用法：

```powershell
python bian.py
python bian.py DOGEUSDT TLMUSDT
python bian.py --symbols DOGEUSDT,TLMUSDT --json
```

## 项目结构

```text
C:\code\bian
  src\bian_dashboard\       Python 后端与行情分析器真实实现
  web\                      前端页面和静态资源
  runtime\                  运行时缓存，不作为源码维护
  scripts\                  启动和验证脚本
  docs\                     架构、验收、回归和风险记录
  backups\                  旧页面/脚本备份
  archive\                  历史项目拷贝
  bian.py                   兼容入口，转发到 src\bian_dashboard\analyzer.py
  server.py                 兼容入口，转发到 src\bian_dashboard\server.py
```

更完整的目录说明见 [docs/architecture.md](docs/architecture.md)。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

验证内容包括 Python 编译、前端 JS 语法检查、分析器 CLI 帮助输出。

## 风险提示

这是技术指标辅助工具，不是自动交易程序，也不能保证盈利。合约网格请低杠杆、控制仓位、设置停止网格或止损。
