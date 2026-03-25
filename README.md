# 韩美PBPK / PKPD 口服制剂 BE 评估系统

本项目是“浏览器单页 + 本地 Python 后端”的运行形态，不是纯前端状态演示。

## 启动

双击 `run.cmd`

或命令行执行：

```bash
node server.js --open
```

## 仓库内容

GitHub 仓库里只提交项目源码、页面文件和需求文档，不提交以下本地运行产物：

- `tools/` 本地嵌入式 Python 运行时
- `external/` 外部开源仓库克隆目录
- 日志、缓存和 `__pycache__/`

这样可以避免把大体积运行时和上游仓库源码整体推到 GitHub。

## 主要文件

- `backend_server.py`：本地 Python 后端
- `index.html`：浏览器入口
- `app.js`：前端逻辑
- `styles.css`：界面样式
- `server.js`：启动 Python 后端的 Node 包装器
- `requirements.txt`：Python 依赖清单

## 本地准备

1. 安装 Python 3.11
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 如需补齐开源仓库，自行克隆到 `external/`：

```bash
git clone https://github.com/Open-Systems-Pharmacology/PK-Sim.git external/PK-Sim
git clone https://github.com/Open-Systems-Pharmacology/MoBi.git external/MoBi
git clone https://github.com/scheckley/pyvivc.git external/pyvivc
git clone https://github.com/Open-Systems-Pharmacology/IVIVC-with-particle-dissolution-module-in-OSP.git external/OSP-IVIVC
```

## 实际工作方式

- 前端页面负责参数输入、任务提交、状态轮询和图表展示
- 后端负责真实计算：
  - 口服吸收/溶解/转运模拟
  - 区域浓度与报表汇总
  - `f2` 因子计算
  - `pyvivc` 体内外相关性回归
  - `bioeq` BE 统计
  - 虚拟 BE Monte Carlo 成功率
- “运行中 / 倒计时 / 阶段提示” 来自后端任务状态，不是固定 UI 效果

## 自检说明

页面“系统自检”会检查：

- Python 运行时
- 已安装科学计算包
- 已下载的开源仓库
- 后端模拟冒烟测试

如果本地缺依赖或缺仓库，自检会直接显示缺口。

## 当前限制

- 当前 PBPK 部分是本地可运行的简化人体吸收/分布模型，不是直接调用 PK-Sim 可执行程序
- 需求文档里提到的 `ivivc` / `bioequivalence` 包名，当前实现使用已验证可运行的 `pyvivc` / `bioeq`
