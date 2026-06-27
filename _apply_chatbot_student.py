# One-off: remove student-chatbot cluster from app.py and wire registration.
# Line ranges verified byte-identical against routes/chatbot_student.py.
# Run once, verify, then delete this file.

with open('app.py', encoding='utf-8') as f:
    lines = f.readlines()  # lines[i] == source line i+1

assert len(lines) == 12067, f'unexpected line count {len(lines)}'

# Sanity anchors (1-based)
assert lines[4552].startswith("@app.route('/student/chatbot')"), lines[4552][:40]
assert lines[4560].startswith("    return render_template('student_chatbot_fullscreen.html')"), lines[4560][:40]
assert lines[6798].startswith('# Cache global pour les documents'), lines[6798][:40]
assert lines[7919].rstrip() == '        ]})', repr(lines[7919])
assert lines[12061].startswith("if __name__ == '__main__':"), lines[12061][:40]

page_comment = "# Route /student/chatbot deplacee vers routes/chatbot_student.py\n"
cluster_comment = "# Cluster chatbot etudiant (helpers + routes API) deplace vers routes/chatbot_student.py\n"
registration = (
    "from routes.chatbot_student import register_chatbot_student_routes\n"
    "register_chatbot_student_routes(app, {\n"
    "    'get_db_connection': get_db_connection,\n"
    "    'login_required': login_required,\n"
    "})\n"
)

new_lines = (
    lines[0:4552]            # 1..4552
    + [page_comment]         # replaces 4553..4561
    + lines[4561:6798]       # 4562..6798
    + [cluster_comment]      # replaces 6799..7920
    + lines[7920:12059]      # 7921..12059 (through grades registration)
    + [registration]
    + lines[12059:]          # 12060..end
)

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f'app.py rewritten: {len(lines)} -> {len(new_lines)} lines')
