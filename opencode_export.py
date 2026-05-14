#!/usr/bin/env python3
"""
OpenCode Conversation History Exporter

Reads the local OpenCode SQLite database and exports all conversation history
(including archived sessions) as HTML reports, organized by project directory.
Uses incremental updates - only exports new or modified sessions.
"""

import json
import os
import re
import sqlite3
import hashlib
import html
from datetime import datetime
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────

OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
EXPORT_DIR = Path(__file__).parent / "opencode_export"
TEMPLATE_FILE = Path(__file__).parent / "opencode_export_template.html"
MANIFEST_FILE = EXPORT_DIR / ".export_manifest.json"


# ─── Database Access ─────────────────────────────────────────────────────────

def get_db_connection():
    """Connect to the OpenCode SQLite database (read-only)."""
    if not OPENCODE_DB.exists():
        raise FileNotFoundError(
            f"OpenCode database not found at {OPENCODE_DB}\n"
            "Make sure OpenCode is installed and has been used at least once."
        )
    conn = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all_sessions(conn):
    """Fetch all sessions including archived ones."""
    cursor = conn.execute("""
        SELECT
            s.id, s.title, s.directory, s.parent_id,
            s.time_created, s.time_updated, s.time_archived,
            s.summary_additions, s.summary_deletions, s.summary_files
        FROM session s
        ORDER BY s.time_created DESC
    """)
    return [dict(row) for row in cursor.fetchall()]


def fetch_messages(conn, session_id):
    """Fetch all messages for a session, ordered by creation time."""
    cursor = conn.execute("""
        SELECT id, data, time_created, time_updated
        FROM message
        WHERE session_id = ?
        ORDER BY time_created ASC
    """, (session_id,))
    return [dict(row) for row in cursor.fetchall()]


def fetch_parts(conn, session_id):
    """Fetch all parts for a session, grouped by message_id."""
    cursor = conn.execute("""
        SELECT id, message_id, data, time_created
        FROM part
        WHERE session_id = ?
        ORDER BY time_created ASC
    """, (session_id,))
    parts_by_message = {}
    for row in cursor.fetchall():
        row = dict(row)
        mid = row["message_id"]
        if mid not in parts_by_message:
            parts_by_message[mid] = []
        parts_by_message[mid].append(row)
    return parts_by_message


# ─── Content Rendering ───────────────────────────────────────────────────────

def escape(text):
    """HTML-escape text."""
    return html.escape(text) if text else ""


def render_markdown_basic(text):
    """Minimal markdown to HTML conversion for display."""
    if not text:
        return ""
    text = escape(text)
    # Code blocks (```)
    text = re.sub(
        r'```(\w*)\n(.*?)```',
        lambda m: f'<pre class="code-block"><code class="language-{m.group(1)}">{m.group(2)}</code></pre>',
        text, flags=re.DOTALL
    )
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code class="inline-code">\1</code>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Headers
    text = re.sub(r'^### (.+)$', r'<h4 class="msg-h4">\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3 class="msg-h3">\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2 class="msg-h2">\1</h2>', text, flags=re.MULTILINE)
    # Line breaks (preserve paragraphs)
    text = re.sub(r'\n\n', '</p><p class="msg-p">', text)
    text = re.sub(r'\n', '<br>', text)
    text = f'<p class="msg-p">{text}</p>'
    return text


def render_part(part_data):
    """Render a single part to HTML."""
    try:
        data = json.loads(part_data) if isinstance(part_data, str) else part_data
    except (json.JSONDecodeError, TypeError):
        return ""

    part_type = data.get("type", "")

    if part_type == "text":
        text = data.get("text", "")
        if not text.strip():
            return ""
        return f'<div class="part-text">{render_markdown_basic(text)}</div>'

    elif part_type == "tool":
        tool_name = data.get("tool", "unknown")
        state = data.get("state", {})
        status = state.get("status", "")
        title = state.get("title", tool_name)
        input_data = state.get("input", {})
        output = state.get("output", "")

        # Summarize tool call
        status_class = "tool-success" if status == "completed" else "tool-pending"
        input_summary = ""
        if isinstance(input_data, dict):
            # Show key parameters briefly
            for k, v in list(input_data.items())[:3]:
                val_str = str(v)[:120]
                input_summary += f'<span class="tool-param">{escape(k)}: {escape(val_str)}</span>'

        output_preview = ""
        if output and isinstance(output, str):
            preview = output[:300]
            if len(output) > 300:
                preview += "..."
            output_preview = f'<pre class="tool-output">{escape(preview)}</pre>'

        return f'''<details class="part-tool {status_class}">
            <summary class="tool-summary">
                <span class="tool-icon">⚙</span>
                <span class="tool-name">{escape(tool_name)}</span>
                <span class="tool-title">{escape(title)}</span>
                <span class="tool-status">{escape(status)}</span>
            </summary>
            <div class="tool-details">
                {f'<div class="tool-input">{input_summary}</div>' if input_summary else ''}
                {output_preview}
            </div>
        </details>'''

    elif part_type == "step-start":
        return '<div class="part-step-start"></div>'

    elif part_type == "step-finish":
        return ""

    elif part_type == "reasoning":
        text = data.get("text", "")
        if not text.strip():
            return ""
        return f'<details class="part-reasoning"><summary>Reasoning</summary><div>{render_markdown_basic(text)}</div></details>'

    elif part_type == "compaction":
        return '<div class="part-compaction"><span>Context compacted</span></div>'

    elif part_type == "patch":
        path = data.get("path", "")
        return f'<div class="part-patch"><span class="patch-icon">📝</span> {escape(path)}</div>'

    elif part_type == "file":
        path = data.get("path", "")
        return f'<div class="part-file"><span class="file-icon">📄</span> {escape(path)}</div>'

    return ""


def render_session_html(session, messages, parts_by_message, template):
    """Render a full session to HTML using the template."""
    title = session["title"] or "Untitled"
    created = datetime.fromtimestamp(session["time_created"] / 1000)
    updated = datetime.fromtimestamp(session["time_updated"] / 1000)
    is_archived = session["time_archived"] is not None
    directory = session["directory"] or ""

    # Build messages HTML
    messages_html = []
    for msg in messages:
        msg_data = json.loads(msg["data"]) if isinstance(msg["data"], str) else msg["data"]
        role = msg_data.get("role", "unknown")
        msg_id = msg["id"]

        parts = parts_by_message.get(msg_id, [])
        parts_html = []
        for part in parts:
            rendered = render_part(part["data"])
            if rendered:
                parts_html.append(rendered)

        if not parts_html:
            continue

        role_label = "User" if role == "user" else "Assistant"
        role_class = f"msg-{role}"

        messages_html.append(f'''
        <div class="message {role_class}">
            <div class="msg-header">
                <span class="msg-role">{role_label}</span>
            </div>
            <div class="msg-body">
                {''.join(parts_html)}
            </div>
        </div>''')

    # Stats
    stats_parts = []
    if session["summary_files"]:
        stats_parts.append(f'{session["summary_files"]} files')
    if session["summary_additions"]:
        stats_parts.append(f'+{session["summary_additions"]}')
    if session["summary_deletions"]:
        stats_parts.append(f'-{session["summary_deletions"]}')
    stats_html = f'<span class="session-stats">{" · ".join(stats_parts)}</span>' if stats_parts else ""

    archived_badge = '<span class="badge-archived">Archived</span>' if is_archived else ""

    # Fill template
    content = template
    content = content.replace("{{title}}", escape(title))
    content = content.replace("{{directory}}", escape(directory))
    content = content.replace("{{created}}", created.strftime("%Y-%m-%d %H:%M"))
    content = content.replace("{{updated}}", updated.strftime("%Y-%m-%d %H:%M"))
    content = content.replace("{{archived_badge}}", archived_badge)
    content = content.replace("{{stats}}", stats_html)
    content = content.replace("{{messages}}", "\n".join(messages_html))
    content = content.replace("{{session_id}}", session["id"])

    return content


# ─── Index Page ──────────────────────────────────────────────────────────────

def render_index_html(project_dir, sessions):
    """Render an index page for a project directory."""
    dir_name = Path(project_dir).name or "root"
    rows = []
    for s in sorted(sessions, key=lambda x: x["time_created"], reverse=True):
        created = datetime.fromtimestamp(s["time_created"] / 1000)
        title = s["title"] or "Untitled"
        archived = '<span class="badge-archived">Archived</span>' if s["time_archived"] else ""
        slug = s["id"]
        rows.append(f'''
        <tr class="session-row" onclick="window.location='{slug}.html'">
            <td class="td-title"><a href="{slug}.html">{escape(title)}</a> {archived}</td>
            <td class="td-date">{created.strftime("%Y-%m-%d %H:%M")}</td>
        </tr>''')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(dir_name)} - OpenCode Export</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fff; color: #37352f; }}
.container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.25rem; }}
.subtitle {{ color: #9b9a97; font-size: 0.875rem; margin-bottom: 2rem; }}
table {{ width: 100%; border-collapse: collapse; }}
.session-row {{ cursor: pointer; transition: background 0.1s; }}
.session-row:hover {{ background: #f7f6f3; }}
.session-row td {{ padding: 0.75rem 0.5rem; border-bottom: 1px solid #e9e9e7; }}
.td-title a {{ color: #37352f; text-decoration: none; font-weight: 500; }}
.td-title a:hover {{ text-decoration: underline; }}
.td-date {{ color: #9b9a97; font-size: 0.8rem; white-space: nowrap; text-align: right; }}
.badge-archived {{ background: #f1f1ef; color: #9b9a97; font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; margin-left: 0.5rem; }}
.back-link {{ color: #9b9a97; font-size: 0.85rem; text-decoration: none; display: inline-block; margin-bottom: 1.5rem; }}
.back-link:hover {{ color: #37352f; }}
</style>
</head>
<body>
<div class="container">
    <a href="../index.html" class="back-link">← All Projects</a>
    <h1>{escape(dir_name)}</h1>
    <p class="subtitle">{escape(project_dir)} · {len(sessions)} sessions</p>
    <table>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
</div>
</body>
</html>'''


def render_root_index(projects_info):
    """Render the root index page listing all projects."""
    rows = []
    for proj_dir, info in sorted(projects_info.items(), key=lambda x: x[1]["latest"], reverse=True):
        dir_name = Path(proj_dir).name or "root"
        slug = info["slug"]
        count = info["count"]
        latest = datetime.fromtimestamp(info["latest"] / 1000)
        rows.append(f'''
        <tr class="session-row" onclick="window.location='{slug}/index.html'">
            <td class="td-title"><a href="{slug}/index.html">{escape(dir_name)}</a></td>
            <td class="td-path">{escape(proj_dir)}</td>
            <td class="td-count">{count}</td>
            <td class="td-date">{latest.strftime("%Y-%m-%d")}</td>
        </tr>''')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenCode Export</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fff; color: #37352f; }}
.container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.25rem; }}
.subtitle {{ color: #9b9a97; font-size: 0.875rem; margin-bottom: 2rem; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; padding: 0.5rem; border-bottom: 2px solid #e9e9e7; color: #9b9a97; font-size: 0.75rem; font-weight: 500; text-transform: uppercase; }}
.session-row {{ cursor: pointer; transition: background 0.1s; }}
.session-row:hover {{ background: #f7f6f3; }}
.session-row td {{ padding: 0.75rem 0.5rem; border-bottom: 1px solid #e9e9e7; }}
.td-title a {{ color: #37352f; text-decoration: none; font-weight: 500; }}
.td-title a:hover {{ text-decoration: underline; }}
.td-path {{ color: #9b9a97; font-size: 0.8rem; }}
.td-count {{ color: #9b9a97; font-size: 0.8rem; text-align: center; }}
.td-date {{ color: #9b9a97; font-size: 0.8rem; white-space: nowrap; text-align: right; }}
</style>
</head>
<body>
<div class="container">
    <h1>OpenCode Export</h1>
    <p class="subtitle">All conversation history · Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
    <table>
        <thead>
            <tr>
                <th>Project</th>
                <th>Path</th>
                <th>Sessions</th>
                <th>Latest</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
</div>
</body>
</html>'''


# ─── Incremental Export Logic ────────────────────────────────────────────────

def load_manifest():
    """Load the export manifest tracking previously exported sessions."""
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, "r") as f:
            return json.load(f)
    return {}


def save_manifest(manifest):
    """Save the export manifest."""
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)


def dir_to_slug(directory):
    """Convert a directory path to a safe folder name."""
    # Use a hash suffix to avoid collisions for similarly-named dirs
    short_hash = hashlib.md5(directory.encode()).hexdigest()[:6]
    name = Path(directory).name or "root"
    # Sanitize
    name = re.sub(r'[^\w\-]', '_', name)
    return f"{name}_{short_hash}"


# ─── Main Export ─────────────────────────────────────────────────────────────

def ensure_template():
    """Create the HTML template file if it doesn't exist."""
    if TEMPLATE_FILE.exists():
        return TEMPLATE_FILE.read_text(encoding="utf-8")

    template = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{title}} - OpenCode</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
:root {
    --text-primary: #37352f;
    --text-secondary: #9b9a97;
    --bg-primary: #ffffff;
    --bg-secondary: #f7f6f3;
    --bg-code: #f7f6f3;
    --border: #e9e9e7;
    --accent: #2eaadc;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    font-size: 15px;
}
.container { max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem; }
.session-header { margin-bottom: 2.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }
.session-title { font-size: 2rem; font-weight: 700; margin: 0 0 0.5rem; }
.session-meta { color: var(--text-secondary); font-size: 0.8rem; display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.badge-archived { background: #fdebec; color: #e03e3e; font-size: 0.7rem; padding: 2px 8px; border-radius: 3px; font-weight: 500; }
.session-stats { background: var(--bg-secondary); padding: 2px 8px; border-radius: 3px; font-size: 0.75rem; }
.back-link { color: var(--text-secondary); font-size: 0.85rem; text-decoration: none; display: inline-block; margin-bottom: 1.5rem; }
.back-link:hover { color: var(--text-primary); }

/* Messages */
.message { margin-bottom: 1.5rem; padding: 1rem 0; }
.message + .message { border-top: 1px solid var(--border); }
.msg-header { margin-bottom: 0.5rem; }
.msg-role { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
.msg-user .msg-role { color: var(--text-primary); }
.msg-assistant .msg-role { color: var(--accent); }
.msg-body { }

/* Parts */
.part-text { margin: 0.5rem 0; }
.msg-p { margin: 0.4em 0; }
.msg-h2 { font-size: 1.4rem; font-weight: 600; margin: 1rem 0 0.5rem; }
.msg-h3 { font-size: 1.2rem; font-weight: 600; margin: 0.8rem 0 0.4rem; }
.msg-h4 { font-size: 1rem; font-weight: 600; margin: 0.6rem 0 0.3rem; }

.code-block {
    background: var(--bg-code);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.8rem 1rem;
    overflow-x: auto;
    font-size: 0.82rem;
    line-height: 1.5;
    margin: 0.6rem 0;
    white-space: pre-wrap;
    word-break: break-word;
}
.inline-code {
    background: var(--bg-code);
    padding: 0.15em 0.4em;
    border-radius: 3px;
    font-size: 0.9em;
}

/* Tool calls */
.part-tool { margin: 0.5rem 0; border: 1px solid var(--border); border-radius: 4px; }
.tool-summary {
    padding: 0.5rem 0.75rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8rem;
    color: var(--text-secondary);
    list-style: none;
}
.tool-summary::-webkit-details-marker { display: none; }
.tool-icon { font-size: 0.9rem; }
.tool-name { font-weight: 600; color: var(--text-primary); }
.tool-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-status { font-size: 0.7rem; padding: 1px 6px; border-radius: 3px; background: #dbeddb; color: #2d6a2d; }
.tool-pending .tool-status { background: #fdecc8; color: #9a6700; }
.tool-details { padding: 0.5rem 0.75rem; border-top: 1px solid var(--border); font-size: 0.8rem; }
.tool-param { display: block; color: var(--text-secondary); margin: 0.2rem 0; word-break: break-all; }
.tool-output { background: var(--bg-code); padding: 0.5rem; border-radius: 3px; font-size: 0.75rem; margin-top: 0.4rem; white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto; }

/* Other parts */
.part-reasoning { margin: 0.5rem 0; border: 1px solid var(--border); border-radius: 4px; font-size: 0.85rem; }
.part-reasoning summary { padding: 0.4rem 0.75rem; cursor: pointer; color: var(--text-secondary); font-size: 0.8rem; }
.part-reasoning > div { padding: 0.5rem 0.75rem; border-top: 1px solid var(--border); }
.part-compaction { margin: 0.5rem 0; text-align: center; color: var(--text-secondary); font-size: 0.75rem; font-style: italic; }
.part-patch, .part-file { margin: 0.3rem 0; font-size: 0.8rem; color: var(--text-secondary); }
.part-step-start { margin: 0.3rem 0; }
</style>
</head>
<body>
<div class="container">
    <a href="index.html" class="back-link">← Back to project</a>
    <div class="session-header">
        <h1 class="session-title">{{title}}</h1>
        <div class="session-meta">
            <span>{{created}}</span>
            <span>→</span>
            <span>{{updated}}</span>
            {{archived_badge}}
            {{stats}}
        </div>
    </div>
    <div class="messages">
        {{messages}}
    </div>
</div>
</body>
</html>'''

    TEMPLATE_FILE.write_text(template, encoding="utf-8")
    print(f"  Created template: {TEMPLATE_FILE}")
    return template


def export():
    """Main export function with incremental updates."""
    print("OpenCode Conversation History Exporter")
    print("=" * 42)

    # Ensure template exists
    template = ensure_template()

    # Connect to database
    conn = get_db_connection()
    print(f"  Database: {OPENCODE_DB}")

    # Load manifest for incremental updates
    manifest = load_manifest()
    print(f"  Previously exported: {len(manifest)} sessions")

    # Fetch all sessions
    sessions = fetch_all_sessions(conn)
    print(f"  Total sessions in DB: {len(sessions)}")

    # Determine which sessions need export (new or updated)
    to_export = []
    for s in sessions:
        sid = s["id"]
        prev = manifest.get(sid)
        if prev is None or prev.get("time_updated") != s["time_updated"]:
            to_export.append(s)

    print(f"  Sessions to export: {len(to_export)}")

    if not to_export:
        print("\n  Everything is up to date. Nothing to export.")
        conn.close()
        # Still regenerate index pages in case of deletions
        _regenerate_indexes(sessions, manifest)
        return

    # Export sessions
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    exported_count = 0

    for i, session in enumerate(to_export, 1):
        sid = session["id"]
        title = session["title"] or "Untitled"
        directory = session["directory"] or "unknown"

        print(f"  [{i}/{len(to_export)}] {title[:50]}")

        # Determine output directory
        slug = dir_to_slug(directory)
        out_dir = EXPORT_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        # Fetch messages and parts
        messages = fetch_messages(conn, sid)
        parts_by_message = fetch_parts(conn, sid)

        # Render HTML
        session_html = render_session_html(session, messages, parts_by_message, template)

        # Write file
        out_file = out_dir / f"{sid}.html"
        out_file.write_text(session_html, encoding="utf-8")

        # Update manifest
        manifest[sid] = {
            "time_updated": session["time_updated"],
            "directory": directory,
            "slug": slug,
            "title": title,
        }
        exported_count += 1

    conn.close()

    # Save manifest
    save_manifest(manifest)

    # Regenerate index pages
    _regenerate_indexes(sessions, manifest)

    print(f"\n  Done! Exported {exported_count} sessions.")
    print(f"  Output: {EXPORT_DIR}/")


def _regenerate_indexes(sessions, manifest):
    """Regenerate all index pages."""
    # Group sessions by directory
    by_dir = {}
    for s in sessions:
        d = s["directory"] or "unknown"
        if d not in by_dir:
            by_dir[d] = []
        by_dir[d].append(s)

    # Project info for root index
    projects_info = {}
    for proj_dir, dir_sessions in by_dir.items():
        slug = dir_to_slug(proj_dir)
        out_dir = EXPORT_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        # Write project index
        index_html = render_index_html(proj_dir, dir_sessions)
        (out_dir / "index.html").write_text(index_html, encoding="utf-8")

        latest = max(s["time_created"] for s in dir_sessions)
        projects_info[proj_dir] = {
            "slug": slug,
            "count": len(dir_sessions),
            "latest": latest,
        }

    # Write root index
    root_index = render_root_index(projects_info)
    (EXPORT_DIR / "index.html").write_text(root_index, encoding="utf-8")


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        export()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        exit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise
