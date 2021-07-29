# 189Cloud-Checkin
中国电信 189 天翼 云盘 网盘 签到 以及推送至TG（Telegram）
# 新版说明
需要的文件只有189Checkin.py一个,其他不要用

依赖包请用项目 https://github.com/BlueSkyXN/requirements-serverless

需要填写的参数有 username,password,TGBOTAPI,TGID

可在云函数环境变量中填写,也可以直接改文件(记得加引号,单双应该都行)


# 说明
天翼云盘自动签到 189checkin 服务器部署教程 https://www.blueskyxn.com/202107/4758.html

天翼云盘自动脚本：用Github Action自动签到抽奖天翼云盘 https://www.blueskyxn.com/202101/3648.html


玩转ServerLess：将Python程序打包成云函数 https://www.blueskyxn.com/202107/4816.html

## checkin.py
原版，不推送，修改命令运行，不修改文件
## checkin-tg.py
修改为tg逐条推送，推送配置需要修改文件，账号密码用外置命令
## 189Checkin.py
TG整合推送精简版，推送配置和账号密码都在文件内

# 备份地址
https://gitlab.com/BlueSkyXN/189Cloud-Checkin
