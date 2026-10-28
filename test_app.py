"""
Flask 博客项目核心函数 pytest 测试用例

测试覆盖范围：
  1. 数据库辅助函数：init_db / get_db / query_db / execute_db
  2. 登录装饰器：login_required（未登录重定向、已登录放行）
  3. 前台路由：index（文章列表）、article_detail（文章详情 + 404）
  4. 认证路由：login（成功/失败）、logout
  5. 后台路由：admin（权限守卫）、new_article（发布/校验/自动摘要）、
     edit_article（编辑/校验/404）、delete_article（删除/404）
  6. 404 错误处理器

运行方式：
  py -3.13 -m pytest test_app.py -v
"""

import os
import tempfile
import sqlite3

import pytest

import app as app_module


# ======================  fixtures  ======================

@pytest.fixture
def tmp_db_path(tmp_path):
    """生成临时数据库文件路径（每个测试函数独立，互不干扰）。"""
    return str(tmp_path / "test_blog.db")


@pytest.fixture(autouse=True)
def patch_db(monkeypatch, tmp_db_path):
    """
    自动 fixture：将 app 模块中的 DATABASE 指向临时文件，
    确保 schema.sql 路径正确，并初始化表结构。
    每个测试函数运行前都会拿到一个全新的空数据库。
    """
    monkeypatch.setattr(app_module, 'DATABASE', tmp_db_path)
    monkeypatch.setattr(
        app_module, 'SCHEMA_FILE',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')
    )
    app_module.init_db()
    yield
    # teardown：清理 Flask g 对象中的数据库连接，避免跨测试泄漏
    with app_module.app.test_request_context():
        db = getattr(app_module.g, '_database', None)
        if db is not None:
            db.close()


@pytest.fixture
def client():
    """Flask 测试客户端。"""
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


@pytest.fixture
def logged_in_client(client):
    """已登录的测试客户端（模拟 admin 登录）。"""
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
    return client


# ---- Mock 图片资源 fixture ----

MOCK_LOGO_CONTENT = b'MOCK_LOGO_PNG_DATA'
MOCK_BANNER_CONTENT = b'MOCK_BANNER_PNG_DATA'


@pytest.fixture
def mock_static_assets(monkeypatch, tmp_path):
    """
    创建临时 static 目录并生成 Mock 图片文件，确保测试不依赖真实图片。

    - 将 app.static_folder 指向临时目录
    - 在 assets/ 下创建 logo.png 和 banner.png 的虚拟文件
    - 测试结束后临时目录自动回收，不污染项目
    """
    static_dir = tmp_path / "mock_static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)

    # 写入 Mock 图片内容（非真实图片，仅用于测试验证）
    (assets_dir / "logo.png").write_bytes(MOCK_LOGO_CONTENT)
    (assets_dir / "banner.png").write_bytes(MOCK_BANNER_CONTENT)

    # 同时复制 style.css 到 Mock 目录，保证 <link> 标签能正常加载
    real_css = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'static', 'style.css'
    )
    if os.path.exists(real_css):
        (static_dir / "style.css").write_bytes(
            open(real_css, 'rb').read()
        )

    # 将 Flask 的 static_folder 指向临时目录
    monkeypatch.setattr(app_module.app, 'static_folder', str(static_dir))

    yield static_dir


def insert_article(title="测试标题", content="# 正文", summary="摘要内容"):
    """辅助函数：直接向数据库插入一篇文章并返回其 id。"""
    conn = sqlite3.connect(app_module.DATABASE)
    cur = conn.execute(
        'INSERT INTO articles (title, content, summary, created_at) '
        'VALUES (?, ?, ?, datetime("now"))',
        (title, content, summary)
    )
    conn.commit()
    article_id = cur.lastrowid
    conn.close()
    return article_id


# ======================  数据库辅助函数  ======================

class TestInitDb:
    """init_db：初始化数据库与表结构。"""

    def test_creates_articles_table(self, tmp_db_path):
        """验证 init_db 后 articles 表存在且包含正确的列。"""
        app_module.init_db()
        conn = sqlite3.connect(tmp_db_path)
        cur = conn.execute("PRAGMA table_info(articles)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert columns == {'id', 'title', 'content', 'summary', 'created_at'}

    def test_idempotent(self, tmp_db_path):
        """验证多次调用 init_db 不会报错（CREATE TABLE IF NOT EXISTS）。"""
        app_module.init_db()
        app_module.init_db()  # 不应抛出异常


class TestGetDb:
    """get_db：获取数据库连接，同一请求上下文内复用。"""

    def test_returns_connection(self):
        """验证返回的是 sqlite3.Connection 对象。"""
        with app_module.app.test_request_context():
            db = app_module.get_db()
            assert isinstance(db, sqlite3.Connection)

    def test_caches_in_g(self):
        """验证同一请求上下文中多次调用返回同一连接对象。"""
        with app_module.app.test_request_context():
            db1 = app_module.get_db()
            db2 = app_module.get_db()
            assert db1 is db2

    def test_row_factory_set(self):
        """验证 row_factory 被设置为 sqlite3.Row（支持列名访问）。"""
        with app_module.app.test_request_context():
            db = app_module.get_db()
            assert db.row_factory is sqlite3.Row


class TestQueryDb:
    """query_db：查询数据库并返回结果。"""

    def test_returns_list_for_multiple(self):
        """验证多行查询返回列表。"""
        insert_article("A", "c1", "s1")
        insert_article("B", "c2", "s2")
        with app_module.app.test_request_context():
            rows = app_module.query_db('SELECT * FROM articles ORDER BY id')
            assert len(rows) == 2
            assert rows[0]['title'] == 'A'

    def test_returns_one_when_flag(self):
        """验证 one=True 时返回单个 Row（而非列表）。"""
        insert_article("单篇", "内容", "摘要")
        with app_module.app.test_request_context():
            row = app_module.query_db(
                'SELECT * FROM articles WHERE title = ?', ("单篇",), one=True
            )
            assert row is not None
            assert row['title'] == '单篇'

    def test_returns_none_when_no_match_and_one_true(self):
        """验证 one=True 时无匹配行返回 None。"""
        with app_module.app.test_request_context():
            row = app_module.query_db(
                'SELECT * FROM articles WHERE id = ?', (999,), one=True
            )
            assert row is None

    def test_returns_empty_list_when_no_rows(self):
        """验证 one=False 时无匹配行返回空列表。"""
        with app_module.app.test_request_context():
            rows = app_module.query_db('SELECT * FROM articles')
            assert rows == []


class TestExecuteDb:
    """execute_db：执行写操作（INSERT/UPDATE/DELETE）并自动提交。"""

    def test_insert(self):
        """验证 execute_db 能正确插入数据。"""
        with app_module.app.test_request_context():
            app_module.execute_db(
                'INSERT INTO articles (title, content, summary, created_at) '
                'VALUES (?, ?, ?, ?)',
                ("标题", "内容", "摘要", "2026-01-01 00:00:00")
            )
            row = app_module.query_db(
                'SELECT * FROM articles WHERE title = ?', ("标题",), one=True
            )
            assert row is not None
            assert row['content'] == '内容'

    def test_update(self):
        """验证 execute_db 能正确更新数据。"""
        aid = insert_article("原标题", "原内容", "原摘要")
        with app_module.app.test_request_context():
            app_module.execute_db(
                'UPDATE articles SET title = ? WHERE id = ?',
                ("新标题", aid)
            )
            row = app_module.query_db(
                'SELECT title FROM articles WHERE id = ?', (aid,), one=True
            )
            assert row['title'] == '新标题'

    def test_delete(self):
        """验证 execute_db 能正确删除数据。"""
        aid = insert_article("待删除", "内容", "摘要")
        with app_module.app.test_request_context():
            app_module.execute_db(
                'DELETE FROM articles WHERE id = ?', (aid,)
            )
            row = app_module.query_db(
                'SELECT * FROM articles WHERE id = ?', (aid,), one=True
            )
            assert row is None


# ======================  登录装饰器  ======================

class TestLoginRequired:
    """login_required：未登录用户重定向到登录页，已登录用户正常访问。"""

    def test_redirects_when_not_logged_in(self, client):
        """未登录访问受保护路由应 302 重定向到 /login。"""
        resp = client.get('/admin')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_allows_when_logged_in(self, logged_in_client):
        """已登录用户访问受保护路由应返回 200。"""
        resp = logged_in_client.get('/admin')
        assert resp.status_code == 200


# ======================  前台路由  ======================

class TestIndex:
    """index（GET /）：文章列表页。"""

    def test_returns_200(self, client):
        """验证首页返回 200 状态码。"""
        resp = client.get('/')
        assert resp.status_code == 200

    def test_shows_empty_tip_when_no_articles(self, client):
        """无文章时应显示"暂无文章"提示。"""
        resp = client.get('/')
        assert '暂无文章' in resp.data.decode('utf-8')

    def test_shows_article_title(self, client):
        """有文章时应在首页显示标题。"""
        insert_article("Flask教程", "# Hello", "学Flask")
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'Flask教程' in html

    def test_shows_summary_and_date(self, client):
        """有文章时应显示摘要和发布时间。"""
        insert_article("标题X", "内容Y", "摘要Z")
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert '摘要Z' in html


class TestArticleDetail:
    """article_detail（GET /article/<id>）：文章详情页。"""

    def test_returns_200_for_existing(self, client):
        """存在的文章应返回 200。"""
        aid = insert_article("详情测试", "# 标题\n\n段落", "摘要")
        resp = client.get(f'/article/{aid}')
        assert resp.status_code == 200

    def test_renders_markdown(self, client):
        """验证 Markdown 被渲染为 HTML（# 转为 <h1>）。"""
        aid = insert_article("MD测试", "# 一级标题", "摘要")
        resp = client.get(f'/article/{aid}')
        html = resp.data.decode('utf-8')
        assert '<h1>' in html
        assert '一级标题' in html

    def test_404_for_nonexistent(self, client):
        """不存在的文章 id 应返回 404。"""
        resp = client.get('/article/9999')
        assert resp.status_code == 404


# ======================  认证路由  ======================

class TestLogin:
    """login（GET/POST /login）：后台登录。"""

    def test_get_returns_login_form(self, client):
        """GET /login 应返回登录表单页面。"""
        resp = client.get('/login')
        assert resp.status_code == 200
        assert '用户名' in resp.data.decode('utf-8')

    def test_post_correct_credentials(self, client):
        """正确的 admin/123456 应登录成功并重定向到 /admin。"""
        resp = client.post('/login', data={
            'username': 'admin', 'password': '123456'
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/admin' in resp.headers['Location']

    def test_sets_session(self, client):
        """登录成功后 session 中应存在 username。"""
        client.post('/login', data={
            'username': 'admin', 'password': '123456'
        })
        with client.session_transaction() as sess:
            assert sess.get('username') == 'admin'

    def test_wrong_password(self, client):
        """密码错误应留在登录页并显示错误消息。"""
        resp = client.post('/login', data={
            'username': 'admin', 'password': 'wrong'
        }, follow_redirects=False)
        assert resp.status_code == 200
        assert '用户名或密码错误' in resp.data.decode('utf-8')

    def test_wrong_username(self, client):
        """用户名错误应留在登录页并显示错误消息。"""
        resp = client.post('/login', data={
            'username': 'nobody', 'password': '123456'
        }, follow_redirects=False)
        assert resp.status_code == 200
        assert '用户名或密码错误' in resp.data.decode('utf-8')

    def test_empty_credentials(self, client):
        """空用户名和密码应留在登录页。"""
        resp = client.post('/login', data={
            'username': '', 'password': ''
        }, follow_redirects=False)
        assert resp.status_code == 200


class TestLogout:
    """logout（GET /logout）：退出登录。"""

    def test_clears_session(self, logged_in_client):
        """退出后 session 中的 username 应被清除。"""
        logged_in_client.get('/logout')
        with logged_in_client.session_transaction() as sess:
            assert 'username' not in sess

    def test_redirects_to_index(self, logged_in_client):
        """退出后应重定向到首页。"""
        resp = logged_in_client.get('/logout', follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/')


# ======================  后台路由  ======================

class TestAdmin:
    """admin（GET /admin）：后台文章管理列表。"""

    def test_guard_redirects_anonymous(self, client):
        """未登录用户访问 /admin 应重定向到登录页。"""
        resp = client.get('/admin')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_shows_article_list(self, logged_in_client):
        """已登录用户应看到后台文章管理表格。"""
        insert_article("后台文章", "内容", "摘要")
        resp = logged_in_client.get('/admin')
        html = resp.data.decode('utf-8')
        assert '文章管理' in html
        assert '后台文章' in html

    def test_shows_empty_tip(self, logged_in_client):
        """无文章时应显示"还没有任何文章"。"""
        resp = logged_in_client.get('/admin')
        assert '还没有任何文章' in resp.data.decode('utf-8')


class TestNewArticle:
    """new_article（GET/POST /admin/new）：发布新文章。"""

    def test_guard_redirects_anonymous(self, client):
        """未登录用户访问发布页应重定向。"""
        resp = client.get('/admin/new')
        assert resp.status_code == 302

    def test_get_shows_form(self, logged_in_client):
        """GET 请求应返回发布文章的表单页面。"""
        resp = logged_in_client.get('/admin/new')
        assert resp.status_code == 200
        assert '发布新文章' in resp.data.decode('utf-8')

    def test_post_creates_article(self, logged_in_client):
        """POST 完整数据应创建文章并重定向到 /admin。"""
        resp = logged_in_client.post('/admin/new', data={
            'title': '新文章',
            'content': '# 正文内容',
            'summary': '这是摘要'
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/admin' in resp.headers['Location']
        # 验证数据已写入数据库
        conn = sqlite3.connect(app_module.DATABASE)
        row = conn.execute(
            'SELECT * FROM articles WHERE title = ?', ("新文章",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[3] == '这是摘要'  # summary 是第 4 列（索引 3）

    def test_post_without_title(self, logged_in_client):
        """缺少标题时应显示错误并留在表单页。"""
        resp = logged_in_client.post('/admin/new', data={
            'title': '', 'content': '内容', 'summary': '摘要'
        })
        assert resp.status_code == 200
        assert '标题不能为空' in resp.data.decode('utf-8')

    def test_post_without_content(self, logged_in_client):
        """缺少内容时应显示错误并留在表单页。"""
        resp = logged_in_client.post('/admin/new', data={
            'title': '标题', 'content': '', 'summary': '摘要'
        })
        assert resp.status_code == 200
        assert '内容不能为空' in resp.data.decode('utf-8')

    def test_auto_summary_when_empty(self, logged_in_client):
        """摘要为空时应自动从内容截取前 150 字。"""
        long_content = 'A' * 200
        logged_in_client.post('/admin/new', data={
            'title': '自动摘要测试',
            'content': long_content,
            'summary': ''
        })
        conn = sqlite3.connect(app_module.DATABASE)
        row = conn.execute(
            'SELECT summary FROM articles WHERE title = ?', ("自动摘要测试",)
        ).fetchone()
        conn.close()
        assert row is not None
        # 摘要应为前150个字符 + 省略号
        assert row[0].startswith('A' * 150)
        assert row[0].endswith('…')


class TestEditArticle:
    """edit_article（GET/POST /admin/edit/<id>）：编辑文章。"""

    def test_guard_redirects_anonymous(self, client):
        """未登录用户访问编辑页应重定向。"""
        resp = client.get('/admin/edit/1')
        assert resp.status_code == 302

    def test_get_shows_form_with_data(self, logged_in_client):
        """GET 请求应返回预填充的编辑表单。"""
        aid = insert_article("原标题", "原内容", "原摘要")
        resp = logged_in_client.get(f'/admin/edit/{aid}')
        html = resp.data.decode('utf-8')
        assert resp.status_code == 200
        assert '原标题' in html
        assert '原内容' in html

    def test_404_for_nonexistent(self, logged_in_client):
        """编辑不存在的文章应返回 404。"""
        resp = logged_in_client.get('/admin/edit/9999')
        assert resp.status_code == 404

    def test_post_updates_article(self, logged_in_client):
        """POST 完整数据应更新文章并重定向。"""
        aid = insert_article("旧标题", "旧内容", "旧摘要")
        resp = logged_in_client.post(f'/admin/edit/{aid}', data={
            'title': '新标题',
            'content': '新内容',
            'summary': '新摘要'
        }, follow_redirects=False)
        assert resp.status_code == 302
        conn = sqlite3.connect(app_module.DATABASE)
        row = conn.execute(
            'SELECT * FROM articles WHERE id = ?', (aid,)
        ).fetchone()
        conn.close()
        assert row[1] == '新标题'
        assert row[2] == '新内容'
        assert row[3] == '新摘要'

    def test_post_without_title(self, logged_in_client):
        """编辑时缺少标题应显示错误。"""
        aid = insert_article("标题", "内容", "摘要")
        resp = logged_in_client.post(f'/admin/edit/{aid}', data={
            'title': '', 'content': '内容', 'summary': '摘要'
        })
        assert resp.status_code == 200
        assert '标题不能为空' in resp.data.decode('utf-8')

    def test_post_without_content(self, logged_in_client):
        """编辑时缺少内容应显示错误。"""
        aid = insert_article("标题", "内容", "摘要")
        resp = logged_in_client.post(f'/admin/edit/{aid}', data={
            'title': '标题', 'content': '', 'summary': '摘要'
        })
        assert resp.status_code == 200
        assert '内容不能为空' in resp.data.decode('utf-8')


class TestDeleteArticle:
    """delete_article（POST /admin/delete/<id>）：删除文章。"""

    def test_guard_redirects_anonymous(self, client):
        """未登录用户尝试删除应重定向。"""
        resp = client.post('/admin/delete/1')
        assert resp.status_code == 302

    def test_deletes_existing(self, logged_in_client):
        """已登录用户删除存在的文章应成功并重定向。"""
        aid = insert_article("待删除", "内容", "摘要")
        resp = logged_in_client.post(
            f'/admin/delete/{aid}', follow_redirects=False
        )
        assert resp.status_code == 302
        conn = sqlite3.connect(app_module.DATABASE)
        row = conn.execute(
            'SELECT * FROM articles WHERE id = ?', (aid,)
        ).fetchone()
        conn.close()
        assert row is None

    def test_404_for_nonexistent(self, logged_in_client):
        """删除不存在的文章应返回 404。"""
        resp = logged_in_client.post('/admin/delete/9999')
        assert resp.status_code == 404

    def test_only_post_allowed(self, logged_in_client):
        """GET 请求不应被接受（路由只允许 POST）。"""
        aid = insert_article("测试", "内容", "摘要")
        resp = logged_in_client.get(f'/admin/delete/{aid}')
        assert resp.status_code == 405


# ======================  404 错误处理  ======================

class TestNotFound:
    """page_not_found：404 错误处理。"""

    def test_returns_404_for_unknown_route(self, client):
        """访问不存在的路由应返回 404 页面。"""
        resp = client.get('/this-route-does-not-exist')
        assert resp.status_code == 404
        assert '404' in resp.data.decode('utf-8')


# ======================  Mock 静态文件服务  ======================

class TestStaticFileServing:
    """
    Mock 静态文件服务测试。

    使用 mock_static_assets fixture 将 static_folder 指向临时目录，
    验证 Mock 图片文件能被正确服务，而非依赖真实图片。
    """

    def test_logo_png_served_with_mock_content(self, client, mock_static_assets):
        """GET /static/assets/logo.png 应返回 200 且内容为 Mock 数据。"""
        resp = client.get('/static/assets/logo.png')
        assert resp.status_code == 200
        assert resp.data == MOCK_LOGO_CONTENT

    def test_banner_png_served_with_mock_content(self, client, mock_static_assets):
        """GET /static/assets/banner.png 应返回 200 且内容为 Mock 数据。"""
        resp = client.get('/static/assets/banner.png')
        assert resp.status_code == 200
        assert resp.data == MOCK_BANNER_CONTENT

    def test_nonexistent_static_file_returns_404(self, client, mock_static_assets):
        """请求不存在的静态文件应返回 404。"""
        resp = client.get('/static/assets/nonexistent.png')
        assert resp.status_code == 404

    def test_mock_content_not_real_image(self, client, mock_static_assets):
        """验证 Mock Logo 内容不是真实 PNG 头部（确保不是真实图片）。"""
        resp = client.get('/static/assets/logo.png')
        # 真实 PNG 以 \x89PNG 开头；Mock 内容以 "MOCK" 开头
        assert not resp.data.startswith(b'\x89PNG')
        assert resp.data.startswith(b'MOCK')

    def test_style_css_still_available(self, client, mock_static_assets):
        """Mock 目录中 style.css 仍可正常加载。"""
        resp = client.get('/static/style.css')
        assert resp.status_code == 200


# ======================  Logo 图片渲染  ======================

class TestLogoRendering:
    """
    Logo 图片渲染测试（base.html）。

    使用 Mock 静态资源确保测试不依赖真实图片文件。
    Logo 定义在 base.html 的导航栏中，所有继承 base.html 的页面
    都应包含 Logo 图片标签。这里通过首页、登录页、后台页三个
    典型页面验证 Logo 在不同上下文中的一致性。
    """

    def test_logo_img_exists_on_homepage(self, client, mock_static_assets):
        """首页导航栏中应包含 Logo <img> 标签。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert '<img' in html
        assert 'nav-logo' in html

    def test_logo_src_points_to_logo_png(self, client, mock_static_assets):
        """Logo 图片的 src 应指向 static/assets/logo.png。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'assets/logo.png' in html

    def test_logo_has_alt_text(self, client, mock_static_assets):
        """Logo <img> 应包含 alt 属性以支持无障碍访问。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'alt="Logo"' in html

    def test_logo_inside_nav_brand(self, client, mock_static_assets):
        """Logo <img> 应嵌套在 class="nav-brand" 的 <a> 标签内部。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        brand_start = html.find('nav-brand')
        logo_pos = html.find('nav-logo', brand_start)
        assert brand_start != -1
        assert logo_pos != -1
        assert logo_pos > brand_start

    def test_brand_text_still_present(self, client, mock_static_assets):
        """Logo 旁应保留 "My Tech Blog" 文字品牌。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'My Tech Blog' in html

    def test_logo_appears_on_login_page(self, client, mock_static_assets):
        """登录页同样继承 base.html，应包含 Logo。"""
        resp = client.get('/login')
        html = resp.data.decode('utf-8')
        assert 'nav-logo' in html
        assert 'assets/logo.png' in html

    def test_logo_appears_on_admin_page(self, logged_in_client, mock_static_assets):
        """后台管理页也应包含 Logo。"""
        resp = logged_in_client.get('/admin')
        html = resp.data.decode('utf-8')
        assert 'nav-logo' in html
        assert 'assets/logo.png' in html

    def test_logo_static_url_serves_mock_data(self, client, mock_static_assets):
        """
        集成验证：首页 HTML 中 Logo 的 src URL 实际请求
        应返回 Mock 内容（而非真实图片），证明测试隔离性。
        """
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        # 提取 Logo 图片的 static URL 路径
        import re
        match = re.search(r'src="(/static/assets/logo\.png)"', html)
        assert match is not None
        img_url = match.group(1)
        # 实际请求该 URL，验证返回 Mock 内容
        img_resp = client.get(img_url)
        assert img_resp.status_code == 200
        assert img_resp.data == MOCK_LOGO_CONTENT


# ======================  Banner 图片渲染  ======================

class TestBannerRendering:
    """
    Banner 图片渲染测试（index.html）。

    使用 Mock 静态资源确保测试不依赖真实图片文件。
    Banner 仅出现在首页文章列表上方，验证图片标签、路径、
    位置顺序及 CSS 类名是否正确。
    """

    def test_banner_div_exists(self, client, mock_static_assets):
        """首页应包含 class="banner" 的容器 div。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'class="banner"' in html

    def test_banner_img_tag_exists(self, client, mock_static_assets):
        """Banner 容器内应包含 <img> 标签。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        banner_start = html.find('class="banner"')
        img_pos = html.find('<img', banner_start)
        assert banner_start != -1
        assert img_pos != -1
        assert img_pos > banner_start

    def test_banner_src_points_to_banner_png(self, client, mock_static_assets):
        """Banner 图片 src 应指向 static/assets/banner.png。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'assets/banner.png' in html

    def test_banner_img_has_banner_img_class(self, client, mock_static_assets):
        """Banner <img> 应带有 class="banner-img"。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'banner-img' in html

    def test_banner_has_alt_text(self, client, mock_static_assets):
        """Banner <img> 应包含 alt 属性以支持无障碍访问。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        assert 'alt="Banner"' in html

    def test_banner_before_article_list(self, client, mock_static_assets):
        """Banner 应出现在文章列表（class="article-list"）之前。"""
        insert_article("文章A", "内容", "摘要")
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        banner_pos = html.find('class="banner"')
        list_pos = html.find('article-list')
        assert banner_pos != -1
        assert list_pos != -1
        assert banner_pos < list_pos

    def test_banner_before_page_title(self, client, mock_static_assets):
        """Banner 应出现在"最新文章"标题之前。"""
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        banner_pos = html.find('class="banner"')
        title_pos = html.find('最新文章')
        assert banner_pos != -1
        assert title_pos != -1
        assert banner_pos < title_pos

    def test_banner_not_on_admin_page(self, logged_in_client, mock_static_assets):
        """Banner 不应出现在后台管理页（仅首页独有）。"""
        resp = logged_in_client.get('/admin')
        html = resp.data.decode('utf-8')
        assert 'class="banner"' not in html

    def test_banner_not_on_login_page(self, client, mock_static_assets):
        """Banner 不应出现在登录页。"""
        resp = client.get('/login')
        html = resp.data.decode('utf-8')
        assert 'class="banner"' not in html

    def test_banner_static_url_serves_mock_data(self, client, mock_static_assets):
        """
        集成验证：首页 HTML 中 Banner 的 src URL 实际请求
        应返回 Mock 内容（而非真实图片），证明测试隔离性。
        """
        resp = client.get('/')
        html = resp.data.decode('utf-8')
        # 提取 Banner 图片的 static URL 路径
        import re
        match = re.search(r'src="(/static/assets/banner\.png)"', html)
        assert match is not None
        img_url = match.group(1)
        # 实际请求该 URL，验证返回 Mock 内容
        img_resp = client.get(img_url)
        assert img_resp.status_code == 200
        assert img_resp.data == MOCK_BANNER_CONTENT
