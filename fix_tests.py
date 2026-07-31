import re

with open('tests/test_review/test_review_web.py', 'r') as f:
    content = f.read()

# Pattern for the common case (8 occurrences): import config -> queue_file -> write
pattern1 = r'''        import kanka_wiki_updater\.core\.config as config

        queue_file = os\.path\.join\(config\.DATA_DIR, 'pending_changes\.json'\)
        with open\(queue_file, 'w'\) as f:
            json\.dump\(queue, f, indent=2\)'''

replacement1 = '''        from kanka_wiki_updater.core import state
        state.save_queue(queue)'''

content = re.sub(pattern1, replacement1, content)

# Pattern for the tmp_path case (test_proposal_with_newlines_does_not_break_html):
# Uses json_mod.dump instead of json.dump and has its own DATA_DIR override
pattern2 = r'''        queue_file = tmp_path / 'pending_changes\.json'
        with open\(queue_file, 'w'\) as f:
            json_mod\.dump\(queue, f, indent=2\)

        import kanka_wiki_updater\.core\.config as config
        from kanka_wiki_updater\.review\.web import create_app'''

replacement2 = '''        from kanka_wiki_updater.core import state
        state.save_queue(queue)

        from kanka_wiki_updater.review.web import create_app'''

content = re.sub(pattern2, replacement2, content)

with open('tests/test_review/test_review_web.py', 'w') as f:
    f.write(content)

print(f'Done. Remaining pending_changes.json refs:')
import subprocess
result = subprocess.run(['grep', '-c', 'pending_changes.json', 'tests/test_review/test_review_web.py'], capture_output=True, text=True)
print(result.stdout.strip())
