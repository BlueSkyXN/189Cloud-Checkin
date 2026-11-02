import time
import re
import json
import html
import base64
import os
import rsa
import requests
import random

# ==================== 配置区 ====================

# 账号配置
accounts = []  # 仅供私有部署硬编码；公开版本保持为空

TG_BOT_TOKEN = ""
TG_CHAT_ID = ""

# 调试配置
DEBUG_MODE = False          # 是否启用调试模式（打印详细过程日志，如 Step 1, Step 2...）
PRINT_RESPONSE_BODY = False  # 只打印白名单字段，避免登录票据进入日志

# ==================== 配置区结束 ====================

BI_RM = list("0123456789abcdefghijklmnopqrstuvwxyz")
B64MAP = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def debug_log(message):
    """调试日志输出"""
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")


def print_response(response, label="Response"):
    """仅输出业务状态，不输出 Cookie、票据、账号和通知正文。"""
    if PRINT_RESPONSE_BODY:
        try:
            body = response.json()
            keys = ("result", "res_code", "errorCode", "ok", "isSign", "netdiskBonus")
            summary = {key: body[key] for key in keys if key in body}
            print("[RESP] {}: HTTP {}, {}".format(label, response.status_code,
                  json.dumps(summary, ensure_ascii=False)))
        except (ValueError, TypeError):
            print("[RESP] {}: HTTP {}, 非 JSON 响应".format(label, response.status_code))


def int2char(a):
    return BI_RM[a]


def b64tohex(a):
    d = ""
    e = 0
    c = 0
    for i in range(len(a)):
        if list(a)[i] != "=":
            v = B64MAP.index(list(a)[i])
            if 0 == e:
                e = 1
                d += int2char(v >> 2)
                c = 3 & v
            elif 1 == e:
                e = 2
                d += int2char(c << 2 | v >> 4)
                c = 15 & v
            elif 2 == e:
                e = 3
                d += int2char(c)
                d += int2char(v >> 2)
                c = 3 & v
            else:
                e = 0
                d += int2char(c << 2 | v >> 4)
                d += int2char(15 & v)
    if e == 1:
        d += int2char(c << 2)
    return d


def rsa_encode(j_rsakey, string):
    rsa_key = f"-----BEGIN PUBLIC KEY-----\n{j_rsakey}\n-----END PUBLIC KEY-----"
    pubkey = rsa.PublicKey.load_pkcs1_openssl_pem(rsa_key.encode())
    result = b64tohex((base64.b64encode(rsa.encrypt(f'{string}'.encode(), pubkey))).decode())
    return result


def load_config(environ=None):
    """每次调用重新读取配置；错误的环境配置不得回退到硬编码账号。"""
    env = os.environ if environ is None else environ
    if "TY_ACCOUNTS" in env:
        try:
            configured_accounts = json.loads(env["TY_ACCOUNTS"])
        except (ValueError, TypeError):
            raise ValueError("TY_ACCOUNTS 必须是有效的 JSON 账号数组") from None
    else:
        configured_accounts = accounts
    if not isinstance(configured_accounts, list) or not configured_accounts:
        raise ValueError("请配置非空的 TY_ACCOUNTS 或 accounts 账号数组")
    validated = []
    for number, account in enumerate(configured_accounts, 1):
        if not isinstance(account, dict) or any(
            not isinstance(account.get(key), str) or not account[key].strip()
            for key in ("username", "password")
        ):
            raise ValueError("第 {} 个账号必须包含非空字符串 username 和 password".format(number))
        validated.append({"username": account["username"], "password": account["password"]})

    # 两项作为一组选择来源，避免环境配置与旧硬编码目标混用。
    if "TG_BOT_TOKEN" in env or "TG_CHAT_ID" in env:
        token, chat_id = env.get("TG_BOT_TOKEN", ""), env.get("TG_CHAT_ID", "")
    else:
        token, chat_id = TG_BOT_TOKEN, TG_CHAT_ID
    token, chat_id = str(token or "").strip(), str(chat_id or "").strip()
    notification = "ready" if token and chat_id else "incomplete" if token or chat_id else "disabled"
    return {"accounts": validated, "token": token, "chat_id": chat_id,
            "notification": notification}


def send_telegram_notification(message, token, chat_id):
    """发送已转义动态内容的 HTML 报告。"""
    if not token or not chat_id:
        return False

    url = "https://api.telegram.org/bot{}/sendMessage".format(token)

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        print_response(response, "Telegram API")
        result = response.json()

        if response.status_code == 200 and result.get("ok") is True:
            print("Telegram 推送成功！")
            return True
        else:
            print("Telegram 推送失败，HTTP {}".format(response.status_code))
            return False
    except Exception as e:
        print("Telegram 推送异常：{}".format(type(e).__name__))
        return False


WEB_URL = "https://cloud.189.cn"
AUTH_URL = "https://open.e.189.cn"
API_URL = "https://api.cloud.189.cn"
APP_ID = "8025431004"
RETURN_URL = "https://m.cloud.189.cn/zhuanti/2020/loginErrorPc/index.html"
TIMEOUT = (10, 15)


class LoginError(Exception):
    def __init__(self, message, reason_code="unknown"):
        super().__init__(message)
        self.reason_code = reason_code


def response_json(response, stage):
    if response.status_code != 200:
        raise LoginError("{}：HTTP {}".format(stage, response.status_code))
    try:
        data = response.json()
    except ValueError:
        raise LoginError("{}：响应不是 JSON".format(stage))
    if not isinstance(data, dict):
        raise LoginError("{}：响应结构异常".format(stage))
    return data


def login(username, password):
    """PC 密码登录；只返回已验证存在 SessionKey 的会话，失败抛出异常。"""
    if not username or not password:
        raise LoginError("账号或密码为空", "invalid_configuration")
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36",
        "Accept": "application/json;charset=UTF-8",
    })
    stage = "PC 登录页"
    try:
        r = s.get(WEB_URL + "/api/portal/unifyLoginForPC.action", params={
            "appId": APP_ID, "clientType": "10020", "returnURL": RETURN_URL,
            "timeStamp": str(int(time.time() * 1000)),
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            raise LoginError("PC 登录页：HTTP {}".format(r.status_code))
        fields = {}
        patterns = {
            "captchaToken": r"['\"]captchaToken['\"]\s+value=['\"]([^'\"]+)['\"]",
            "lt": r"\blt\s*=\s*['\"]([^'\"]+)['\"]",
            "paramId": r"\bparamId\s*=\s*['\"]([^'\"]+)['\"]",
            "reqId": r"\breqId\s*=\s*['\"]([^'\"]+)['\"]",
        }
        for name, pattern in patterns.items():
            match = re.search(pattern, r.text)
            if not match:
                raise LoginError("PC 登录页缺少字段：{}".format(name))
            fields[name] = match.group(1)

        stage = "获取加密配置"
        r = s.post(AUTH_URL + "/api/logbox/config/encryptConf.do",
                   data={"appId": APP_ID}, timeout=TIMEOUT)
        conf = response_json(r, stage)
        encryption = conf.get("data") or {}
        if conf.get("result") not in (0, "0") or not encryption.get("pubKey") or not encryption.get("pre"):
            raise LoginError("加密配置缺少公钥或前缀")
        encrypted_user = encryption["pre"] + rsa_encode(encryption["pubKey"], username)
        encrypted_password = encryption["pre"] + rsa_encode(encryption["pubKey"], password)
        auth_headers = {"Referer": AUTH_URL + "/", "lt": fields["lt"], "REQID": fields["reqId"]}

        stage = "验证码检查"
        r = s.post(AUTH_URL + "/api/logbox/oauth2/needcaptcha.do", headers=auth_headers,
                   data={"appKey": APP_ID, "accountType": "02", "userName": encrypted_user},
                   timeout=TIMEOUT)
        if r.status_code != 200:
            raise LoginError("验证码检查：HTTP {}".format(r.status_code))
        captcha = r.text.strip()
        if captcha == "1":
            raise LoginError("需要验证码；停止自动密码登录", "captcha_required")
        if captcha != "0":
            raise LoginError("验证码检查返回未知结果；停止自动密码登录")

        stage = "密码登录"
        r = s.post(AUTH_URL + "/api/logbox/oauth2/loginSubmit.do", headers=auth_headers, data={
            "appKey": APP_ID, "accountType": "02", "userName": encrypted_user,
            "password": encrypted_password, "validateCode": "",
            "captchaToken": fields["captchaToken"], "dynamicCheck": "FALSE",
            "clientType": "1", "cb_SaveName": "3", "isOauth2": "false",
            "returnUrl": RETURN_URL, "paramId": fields["paramId"],
        }, timeout=TIMEOUT)
        data = response_json(r, stage)
        code = data.get("result")
        if not (type(code) is int or isinstance(code, str) and re.fullmatch(r"-?[0-9]+", code)):
            raise LoginError("密码登录响应缺少有效结果码")
        code = int(code)
        if code == -133:
            raise LoginError("需要设备二次验证；停止自动密码登录", "device_verification_required")
        if code != 0:
            # 不输出可能含票据的完整响应或请求 URL。
            message = str(data.get("msg", "未知错误"))
            for value in (str(username), str(password)):
                message = message.replace(value, "[REDACTED]")
            message = re.sub(r"https?://\S+", "[URL]", message)
            raise LoginError("密码登录被拒绝（{}）：{}".format(code, message[:180]), "login_rejected")
        if not isinstance(data.get("toUrl"), str) or not data["toUrl"]:
            raise LoginError("密码登录响应缺少有效 toUrl")

        stage = "换取 PC 会话"
        r = s.post(API_URL + "/getSessionForPC.action", params={
            "appId": APP_ID, "clientType": "TELEPC", "version": "6.2",
            "channelId": "web_cloud.189.cn", "rand": str(int(time.time() * 1000)),
            "redirectURL": data["toUrl"],
        }, timeout=TIMEOUT)
        data = response_json(r, stage)
        if data.get("res_code") not in (0, "0", None) or not data.get("sessionKey"):
            raise LoginError("PC 会话无效或缺少 sessionKey")
        s.cloud189_session_key = data["sessionKey"]
        return s
    except Exception as exc:
        s.close()
        if isinstance(exc, LoginError):
            raise
        raise LoginError("{}：{}".format(stage, type(exc).__name__)) from None


def get_capacity(s):
    """只读查询，用于验证登录和 Web Session 鉴权。"""
    r = s.get(WEB_URL + "/api/portal/getUserSizeInfo.action",
              params={"sessionKey": s.cloud189_session_key}, timeout=TIMEOUT)
    data = response_json(r, "容量查询")
    if not isinstance(data.get("cloudCapacityInfo"), dict):
        raise LoginError("容量查询失败，未获得有效业务会话")
    return data["cloudCapacityInfo"]


def error_text(exc):
    # requests 异常可能包含完整 URL（含 SessionKey 或 Bot Token）。
    return str(exc) if isinstance(exc, LoginError) else type(exc).__name__


def user_sign(s):
    """仅发起一次个人签到，不抽奖、不推送、不自动重试。"""
    params = {
        "sessionKey": s.cloud189_session_key,
        "rand": str(int(time.time() * 1000)),
        "clientType": "TELEANDROID", "version": "9.0.6", "model": "KB2000",
    }
    response = s.get(WEB_URL + "/mkt/userSign.action", params=params,
                     headers={"Referer": WEB_URL + "/web/main/"}, timeout=TIMEOUT)
    print_response(response, "签到")
    data = response_json(response, "签到")
    error_code = data.get("errorCode")
    if error_code is not None and type(error_code) not in (str, int):
        raise LoginError("签到响应包含无效错误码")
    if error_code not in (None, 0, "0", ""):
        raise LoginError("签到接口返回业务错误", "sign_rejected")
    if "netdiskBonus" not in data or "isSign" not in data:
        raise LoginError("签到响应缺少有效业务结果")
    state = data["isSign"]
    if state is not False and state is not True and state not in ("false", "true"):
        raise LoginError("签到状态未知")
    return data


def do_checkin(s, username):
    """仅执行个人签到，返回可独立汇总的账号结果。"""
    result = {"username": username, "signin": "", "status": "failed", "error": None,
              "reason_code": "unknown"}
    try:
        data = user_sign(s)
        if data["isSign"] is False or data["isSign"] == "false":
            result["status"] = "signed"
            result["signin"] = "签到成功，获得 {}M 空间".format(data["netdiskBonus"])
        else:
            result["status"] = "already_signed"
            result["signin"] = "今日已签到，本次未重复领取"
        result["reason_code"] = result["status"]
    except Exception as exc:
        result["error"] = "签到失败或结果未知：{}".format(error_text(exc))
        result["reason_code"] = exc.reason_code if isinstance(exc, LoginError) else "unknown"
    return result


def process_account(account, index, total):
    username = account["username"]
    masked = username[:3] + "***" + username[-2:] if len(username) > 5 else "***"
    print("账号 [{}/{}]：{}".format(index, total, masked))
    s = None
    try:
        s = login(username, account["password"])
        result = do_checkin(s, masked)
    except Exception as exc:
        result = {"username": masked, "signin": "", "status": "failed",
                  "error": "登录失败：{}".format(error_text(exc)),
                  "reason_code": exc.reason_code if isinstance(exc, LoginError) else "unknown"}
    finally:
        if s is not None:
            s.close()
    print(result["error"] or result["signin"])
    return result


def summarize(all_results):
    counts = {"signed": 0, "already_signed": 0, "failed": 0}
    for result in all_results:
        counts[result["status"]] += 1
    return counts


def format_notification_message(all_results):
    lines = ["🌥️ <b>天翼云盘签到报告</b>", ""]
    icons = {"signed": "🎉", "already_signed": "✅", "failed": "❌"}
    for result in all_results:
        username = html.escape(str(result["username"]))
        detail = html.escape(str(result["error"] or result["signin"]))
        lines.extend(["{} <b>{}</b>".format(icons[result["status"]], username),
                      detail, ""])
    counts = summarize(all_results)
    lines.extend(["📊 <b>统计 · 共 {} 个账号</b>".format(len(all_results)),
                  "🎉 本次签到 {}  ·  ✅ 今日已签到 {}  ·  ❌ 失败或未知 {}".format(
                      counts["signed"], counts["already_signed"], counts["failed"])])
    return "\n".join(lines)


def main():
    config = load_config()
    configured_accounts = config["accounts"]
    notification = config["notification"]
    print("天翼云盘个人签到，账号数量：{}".format(len(configured_accounts)))
    if notification == "incomplete":
        print("Telegram 配置不完整，跳过推送；不影响签到")
    elif notification == "disabled":
        print("Telegram 未启用")

    all_results = []
    for i, account in enumerate(configured_accounts):
        all_results.append(process_account(account, i + 1, len(configured_accounts)))
        if i < len(configured_accounts) - 1:
            time.sleep(random.uniform(1, 3))

    message = format_notification_message(all_results)
    print(message)
    if notification == "ready":
        sent = send_telegram_notification(message, config["token"], config["chat_id"])
        notification = "sent" if sent else "failed"
    counts = summarize(all_results)
    return {"ok": counts["failed"] == 0, "counts": counts,
            "notification": notification, "results": all_results}


def main_handler(event, context):
    """返回兼容的 statusCode/body 信封，业务结果与通知结果分别报告。

    results 的 status 和 counts 保持 signed/already_signed/failed 三类。
    新增 reason_code：signed、already_signed 表示有效签到结果；
    captcha_required、device_verification_required 表示需要人工认证；
    login_rejected、sign_rejected 表示接口明确拒绝，不等于密码失效；
    unknown 表示网络异常、无效响应或没有可靠业务证据，不应自动重跑。
    配置错误在 body 顶层返回 reason_code=invalid_configuration。
    counts.failed 仍包含需要认证和结果未知项；调用方不能仅凭 failed
    判定远端业务失败，也不能通过 error 中文文本或通知结果推断业务状态。
    """
    try:
        result = main()
    except ValueError as exc:
        result = {"ok": False, "config_error": str(exc), "notification": "not_attempted",
                  "reason_code": "invalid_configuration"}
        print(result["config_error"])
    return {"statusCode": 200, "body": json.dumps(result, ensure_ascii=False)}


if __name__ == "__main__":
    main()
