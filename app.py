import sqlite3
import os
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, abort
)
import markdown

app = Flask(__name__)
app.secret_key = 'change-me-in-production-please'

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blog.db')
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = '123456'


# ----------------------- 数据库辅助函数 -----------------------

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    """初始化数据库：如果库或表不存在则创建。"""
    conn = sqlite3.connect(DATABASE)
    with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    conn = get_db()
    conn.execute(query, args)
    conn.commit()


# ----------------------- 登录装饰器 -----------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# ----------------------- 首页 / 文章列表 -----------------------

@app.route('/')
def index():
    articles = query_db(
        'SELECT id, title, summary, created_at FROM articles ORDER BY id DESC'
    )
    return render_template('index.html', articles=articles)


# ----------------------- 文章详情 -----------------------

@app.route('/article/<int:article_id>')
def article_detail(article_id):
    art = query_db('SELECT * FROM articles WHERE id = ?', (article_id,), one=True)
    if art is None:
        abort(404)
    html_content = markdown.markdown(
        art['content'],
        extensions=['extra', 'codehilite', 'tables', 'fenced_code']
    )
    return render_template('article.html', article=art, html_content=html_content)


# ----------------------- 登录 / 登出 -----------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['username'] = username
            flash('登录成功！', 'success')
            return redirect(url_for('admin'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    session.pop('username', None)
    flash('已退出登录', 'info')
    return redirect(url_for('index'))


# ----------------------- 后台：文章列表 -----------------------

@app.route('/admin')
@login_required
def admin():
    articles = query_db(
        'SELECT id, title, summary, created_at FROM articles ORDER BY id DESC'
    )
    return render_template('admin.html', articles=articles)


# ----------------------- 后台：发布新文章 -----------------------

@app.route('/admin/new', methods=['GET', 'POST'])
@login_required
def new_article():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '')
        summary = request.form.get('summary', '').strip()

        if not title:
            flash('标题不能为空', 'error')
            return render_template('edit.html', action='new', article=None)
        if not content:
            flash('内容不能为空', 'error')
            return render_template('edit.html', action='new', article=None)
        if not summary:
            summary = content[:150].replace('\n', ' ')
            if len(content) > 150:
                summary += '…'

        execute_db(
            'INSERT INTO articles (title, content, summary, created_at) VALUES (?, ?, ?, ?)',
            (title, content, summary, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        flash('文章发布成功！', 'success')
        return redirect(url_for('admin'))

    return render_template('edit.html', action='new', article=None)


# ----------------------- 后台：编辑文章 -----------------------

@app.route('/admin/edit/<int:article_id>', methods=['GET', 'POST'])
@login_required
def edit_article(article_id):
    art = query_db('SELECT * FROM articles WHERE id = ?', (article_id,), one=True)
    if art is None:
        abort(404)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '')
        summary = request.form.get('summary', '').strip()

        if not title:
            flash('标题不能为空', 'error')
            return render_template('edit.html', action='edit', article=art)
        if not content:
            flash('内容不能为空', 'error')
            return render_template('edit.html', action='edit', article=art)
        if not summary:
            summary = content[:150].replace('\n', ' ')
            if len(content) > 150:
                summary += '…'

        execute_db(
            'UPDATE articles SET title = ?, content = ?, summary = ? WHERE id = ?',
            (title, content, summary, article_id)
        )
        flash('文章已更新！', 'success')
        return redirect(url_for('admin'))

    return render_template('edit.html', action='edit', article=art)


# ----------------------- 后台：删除文章 -----------------------

@app.route('/admin/delete/<int:article_id>', methods=['POST'])
@login_required
def delete_article(article_id):
    art = query_db('SELECT id FROM articles WHERE id = ?', (article_id,), one=True)
    if art is None:
        abort(404)
    execute_db('DELETE FROM articles WHERE id = ?', (article_id,))
    flash('文章已删除', 'info')
    return redirect(url_for('admin'))


# ----------------------- 404 -----------------------

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


# ----------------------- 启动入口 -----------------------

if __name__ == '__main__':
    init_db()
    print('数据库初始化完成。')
    print('后台登录账号: admin / 123456')
    app.run(host='0.0.0.0', port=5000, debug=True)
