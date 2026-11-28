"""モニターの認証状態を、製品データを含まない画面で案内する。"""
from html import escape
from importlib import resources
from string import Template


_MESSAGES = {
    "login": ("モニターにログイン", "利用を許可された Google アカウントでログインしてください。"),
    "cancelled": ("ログインをキャンセルしました", "モニターを開くには、もう一度ログインしてください。"),
    "denied": ("このアカウントでは利用できません", "利用を許可された別の Google アカウントを選んでください。"),
    "unavailable": ("現在ログインできません", "時間をおいて、もう一度ログインしてください。"),
}


def render_auth_page(state: str, login_url: str) -> bytes:
    """login_url は認証ルートが生成した同一オリジンの開始リンク。"""
    heading, message = _MESSAGES[state]
    web = resources.files("ai_yori_murasaki").joinpath("web")
    template = Template(web.joinpath("monitor-login.html").read_text(encoding="utf-8"))
    return template.substitute(
        heading=heading, message=message, login_url=escape(login_url, quote=True),
    ).encode("utf-8")


def add_logout(html: bytes, csrf_token: str) -> bytes:
    """ログイン済みの4画面に、サーバーが検証するフォームを置く。"""
    form = (
        '<form class="session-form" action="/auth/logout" method="post">'
        f'<input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}">'
        '<button type="submit">ログアウト</button></form>'
    )
    return html.replace(b"<!-- monitor-session -->", form.encode("utf-8"), 1)
