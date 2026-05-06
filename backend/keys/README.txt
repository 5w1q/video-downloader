试用期（给对方 Docker 部署用）
================================

本目录若存在 trial_public.pem，后端启动时会要求环境变量 TRIAL_LICENSE 为有效 JWT，
过期后进程会直接退出。

你（作者）在本机操作：
1) pip install cryptography PyJWT
2) python scripts/generate_trial_keys.py
3) python scripts/issue_trial_license.py --days 3
4) 把 trial_public.pem 复制进给对方构建的镜像（例如 Dockerfile COPY keys/trial_public.pem），
   把 issue 脚本打印的 TRIAL_LICENSE=... 写入对方的 .env

公开 Git 仓库请勿提交 trial_private.pem / trial_public.pem（已在 .gitignore）。
给对方的是：含公钥的镜像 + 你签发的 TRIAL_LICENSE，不要给私钥。

延长 / 续期 3 天（本机有私钥时）：
  python scripts/issue_trial_license.py --days 3
  将输出的 TRIAL_LICENSE=... 写入对方使用的 .env（或 docker-compose environment），重启后端。

彻底放开无限制（不再校验试用期）——任选其一：
  A) 部署里去掉 trial_public.pem：镜像不再 COPY/挂载该文件，或从容器内删除该路径；
     可一并删除环境变量 TRIAL_LICENSE。重启后 trial_gate 不启用。
  B) 仍保留公钥但长期有效：本机执行
       python scripts/issue_trial_license.py --days 3650
     把新 token 写入 TRIAL_LICENSE 并重启（适合“正式授权”仍走同一套 JWT 的场景）。
