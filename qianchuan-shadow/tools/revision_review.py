"""Inspect revision evidence or append a review note. No approval/clearance command."""
import argparse,json
from pathlib import Path
from qc_shadow.revision_review import add_note
p=argparse.ArgumentParser();p.add_argument('--home',required=True);sub=p.add_subparsers(dest='command',required=True);sub.add_parser('show');n=sub.add_parser('note');n.add_argument('--revision-id',required=True);n.add_argument('--text',required=True);a=p.parse_args()
if a.command=='show':print((Path(a.home)/'queue.json').read_text())
else:print(json.dumps({'note_file':add_note(a.home,a.revision_id,a.text),'execution_clearance':False},ensure_ascii=False))
