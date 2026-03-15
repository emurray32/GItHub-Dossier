"""One-time cleanup: delete all old scan-imported intent signals and start fresh."""
from database import db_connection

with db_connection() as conn:
    cur = conn.cursor()

    cur.execute('SELECT COUNT(*) FROM intent_signals')
    before = cur.fetchone()[0]
    print(f'Intent signals before: {before}')

    cur.execute('DELETE FROM drafts')
    cur.execute('DELETE FROM prospects')
    cur.execute('DELETE FROM feedback_log')
    cur.execute('DELETE FROM activity_log')
    cur.execute('DELETE FROM intent_signals')
    conn.commit()

    cur.execute('SELECT COUNT(*) FROM intent_signals')
    after = cur.fetchone()[0]
    print(f'Intent signals after: {after}')
    print('Done — clean slate. Upload fresh signals via CSV/Excel.')
