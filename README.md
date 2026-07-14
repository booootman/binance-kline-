# Binance Futures Dashboard

本项目是一个 Binance U 本位合约行情分析仪表盘。后端负责拉取行情、计算指标、生成策略快照和实时价格流；前端负责展示多周期建议、实时价格、风险提示、K 线参考、信号历史和实盘复盘。

它是辅助看盘工具，不是自动交易系统，也不保证盈利。合约交易请低杠杆、控制仓位、设置止损。

## 快速启动

```powershell
cd C:\code\bian
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

打开：

```text
http://127.0.0.1:8000/binance-futures-dashboard.html
```

命令行分析器仍兼容旧入口：

```powershell
python bian.py
python bian.py DOGEUSDT TLMUSDT
python bian.py --symbols DOGEUSDT,TLMUSDT --json
```

## 项目结构

```text
C:\code\bian
  src\bian_dashboard\       Python 后端和行情分析器
  web\                      前端页面和静态资源
  runtime\                  运行时缓存，不作为源码维护
  scripts\                  启动、验证、部署脚本
  docs\                     架构、部署、验收和风险记录
  backups\                  历史备份，默认不改动
  archive\                  历史归档，默认不改动
  bian.py                   兼容入口，转发到 src\bian_dashboard\analyzer.py
  server.py                 兼容入口，转发到 src\bian_dashboard\server.py
```

更完整的说明见 [docs/architecture.md](docs/architecture.md) 和 [docs/docker-deploy.md](docs/docker-deploy.md)。

## 登录和存储

Docker 部署默认启用登录系统，并使用 MySQL 保存用户、会话、偏好、策略快照和信号复盘记录，使用 Redis 保存短期行情缓存和实时价格快照。

本地开发如果没有 MySQL/Redis，前端会回退到浏览器 `localStorage`，运行时文件保存在 `runtime/`。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

验证内容包括：

- Python 编译检查。
- 前端 JavaScript 语法检查。
- 分析器 CLI 帮助输出。
- 离线 smoke 回归测试。

## 部署

```powershell
python scripts\deploy.py
```

默认通过 Docker Compose 部署 `dashboard + MySQL + Redis`。部署前请确认服务器 `.env` 中的登录密码、MySQL 密码和公网端口配置。
如果直接运行 `docker compose up -d --build`，请先复制 `.env.example` 为 `.env` 并设置 `BIAN_AUTH_BOOTSTRAP_PASSWORD`；否则空数据库会出现登录页但没有可用管理员，`/api/health` 会返回 `auth.issue=first_admin_secret_missing`。
