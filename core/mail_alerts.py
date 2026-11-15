"""QQ SMTP alerts with Windows user-scoped credential encryption."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
import hashlib
import os
import re
import smtplib
import ssl
import tempfile

import pywintypes
import win32cred
import win32crypt
import winerror


def qq_address(value):
    return bool(re.fullmatch(r"[\w.-]+@qq\.com", value, re.ASCII | re.IGNORECASE))


def credential_target(base_dir, sender):
    identity = str(base_dir.resolve()).casefold() + "|" + sender.casefold()
    return "Jiansz/QQMail/" + hashlib.sha256(identity.encode()).hexdigest()


def get_code(base_dir, sender):
    target = credential_target(base_dir, sender)
    path = base_dir / (target.rsplit("/", 1)[1] + ".qq-mail.dpapi")
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        pass
    else:
        return win32crypt.CryptUnprotectData(blob, target.encode(), None, None, 1)[1].decode("utf-8")
    try:
        item = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as exc:
        if exc.winerror == winerror.ERROR_NOT_FOUND:
            return ""
        raise
    return item["CredentialBlob"].decode("utf-16-le")


def store_code(base_dir, sender, code):
    target = credential_target(base_dir, sender)
    path = base_dir / (target.rsplit("/", 1)[1] + ".qq-mail.dpapi")
    blob = win32crypt.CryptProtectData(code.encode("utf-8"), "Jiansz QQ SMTP", target.encode(), None, None, 1)
    with tempfile.NamedTemporaryFile(dir=base_dir, delete=False) as pending:
        temp = path.with_name(os.path.basename(pending.name))
        try:
            pending.write(blob)
            pending.close()
            os.replace(temp, path)
        finally:
            pending.close()
            temp.unlink(missing_ok=True)


def send_mail(sender, recipient, code, subject, body, *, html_body=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=6, context=ssl.create_default_context()) as smtp:
        smtp.login(sender, code)
        smtp.send_message(message)


def summarize_outcomes(tasks, reports):
    if not tasks or all(reports.get(task_id, {}).get("result") == "success" for task_id, _ in tasks):
        return ""
    return "\n".join(
        f"{name}：{format_outcome(reports.get(task_id))}"
        for task_id, name in tasks
    )


def format_outcome(report):
    if not report:
        return "本轮无执行结果"
    result = {"success": "日志确认完成", "unknown": "结果未确认", "failed": "失败", "stopped": "已停止"}
    label = result[report["result"]]
    status = report.get("status", "")
    detail = f"（{status}）" if status and status != label else ""
    elapsed = f"，耗时 {report['elapsed']}" if report.get("elapsed") else ""
    return label + detail + elapsed


_OUTCOME_STYLE = {
    "success": ("日志确认完成", "#11664e", "#e8f7ee"),
    "unknown": ("结果未确认", "#8b470e", "#fff2dd"),
    "failed": ("失败", "#a12836", "#fdecee"),
    "stopped": ("已停止", "#8b470e", "#fff2dd"),
    "missing": ("无执行结果", "#8b470e", "#fff2dd"),
    "update": ("启动前检查", "#8b470e", "#fff2dd"),
}


def render_mail(title, description, rows=(), *, counts=(), session_id="", sent_at=None):
    """Render the HTML alternative using layouts supported by mobile mail clients."""
    beijing = timezone(timedelta(hours=8))
    sent_at = sent_at.astimezone(beijing) if sent_at else datetime.now(beijing)
    stamp = sent_at.strftime("%Y.%m.%d · %H:%M 北京时间")
    metrics = "".join(
        f'<td width="{100 // len(counts)}%" style="padding:8px 12px 8px 0;vertical-align:top">'
        f'<div style="font-size:13px;color:#495d76">{escape(label)}</div>'
        f'<strong style="font-size:28px;color:{color}">{value}</strong></td>'
        for label, value, color in counts
    )
    metrics_html = (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>{metrics}</tr></table>'
        if metrics else ""
    )
    cards = []
    for name, result, detail, elapsed in rows:
        label, color, background = _OUTCOME_STYLE[result]
        notes = escape(detail).replace("\n", "<br>")
        notes_html = (
            f'<div style="margin-top:7px;font-size:14px;line-height:1.6;color:#495d76;word-break:break-word">{notes}</div>'
            if notes and detail != label else ""
        )
        duration_html = (
            f'<div style="margin-top:10px;font-size:12px;color:#495d76">耗时 {escape(elapsed)}</div>'
            if elapsed else ""
        )
        cards.append(f'''<tr><td style="padding-top:12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="border:1px solid #dbe4ef;border-radius:12px;background-color:#ffffff">
<tr><td style="padding:16px">
<span style="display:inline-block;padding:3px 9px;border-radius:12px;font-size:12px;font-weight:bold;color:{color};background-color:{background}">{label}</span>
<div style="margin-top:8px;font-size:16px;font-weight:bold;word-break:break-word">{escape(name)}</div>
{notes_html}{duration_html}</td></tr></table></td></tr>''')
    details = "".join(cards)
    session_html = (
        f'<div style="margin-top:6px;overflow-wrap:anywhere;word-break:break-all">运行编号 {escape(session_id)}</div>'
        if session_id else ""
    )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background-color:#edf2f8;color:#152841;font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#edf2f8">
<tr><td align="center" style="padding:16px 10px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;text-align:left">
<tr><td style="padding:6px 8px 16px;font-size:18px;font-weight:bold">简小松 <span style="font-size:13px;font-weight:normal;color:#495d76">· 脚本调度助手</span></td></tr>
<tr><td bgcolor="#152841" style="padding:24px 22px;border-radius:16px 16px 0 0;color:#ffffff">
<h1 style="margin:0;font-size:25px;line-height:1.35">{escape(title)}</h1>
<p style="margin:10px 0 14px;font-size:15px;line-height:1.55;color:#e0eafa">{escape(description)}</p>
<div style="font-size:12px;color:#cadaef">{stamp}</div></td></tr>
<tr><td bgcolor="#ffffff" style="padding:14px 18px 20px;border-radius:0 0 16px 16px">
{metrics_html}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{details}</table>
<div style="margin-top:16px;padding-top:12px;border-top:1px solid #dbe4ef;font-size:12px;line-height:1.5;color:#495d76">
完整记录可在简小松的「运行统计」和「运行日志」查看。{session_html}</div>
</td></tr></table></td></tr></table></body></html>'''


def render_run_mail(tasks, reports, status, session_id):
    rows = []
    completed = 0
    for task_id, name in tasks:
        report = reports.get(task_id) or {}
        result = report.get("result", "missing")
        completed += result == "success"
        rows.append((name, result, report.get("status", ""), report.get("elapsed", "")))
    attention = len(tasks) - completed
    rows.sort(key=lambda row: row[1] == "success")
    title = f"{attention} 项任务需要关注" if attention else "本轮运行有异常"
    return render_mail(title, status, rows, session_id=session_id, counts=(
        ("需要关注", attention, "#98510f"), ("日志确认完成", completed, "#11664e"),
    ))
