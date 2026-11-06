"""Local knowledge indexing and grounded OpenAI-compatible chat. No platform actions."""
from contextlib import contextmanager
import argparse
import json
import re
import sqlite3
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE / 'data' / 'knowledge.db'

from .prompts import BRIEFING_SYSTEM_PROMPT


def sections(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in ('.md', '.txt'):
        return [('正文', path.read_text(encoding='utf-8-sig'))]
    if suffix == '.docx':
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return [('正文', '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns)))]
    if suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF导入需要安装 pypdf：python -m pip install --target vendor pypdf') from None
        return [('第%d页' % (i + 1), p.extract_text() or '') for i, p in enumerate(PdfReader(path).pages)]
    raise ValueError('支持 .docx、.pdf、.md、.txt；旧版 .doc 请另存为 .docx')


def tokens(text):
    words = re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', text.lower())
    result = set()
    for word in words:
        if re.fullmatch(r'[\u4e00-\u9fff]+', word):
            result.update(word[i:i+2] for i in range(len(word)-1))
            if len(word) == 1:
                result.add(word)
        else:
            result.add(word)
    return result


class Knowledge:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.report_snapshot = None
        self.last_context = None

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS chunks (source TEXT, location TEXT, text TEXT)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, path):
        path = Path(path).resolve()
        chunks = []
        for location, text in sections(path):
            text = text.strip()
            for start in range(0, len(text), 700):
                chunks.append((str(path), location + ' 段%d' % (start // 700 + 1), text[start:start+900]))
        if not chunks:
            raise ValueError('未提取到文字；扫描PDF需先进行OCR，原索引未改动')
        with self.connect() as db:
            db.execute('DELETE FROM chunks WHERE source=?', (str(path),))
            db.executemany('INSERT INTO chunks VALUES (?,?,?)', chunks)
        return len(chunks)

    def search(self, question):
        if not self.path.exists():
            return []
        query = tokens(question)
        if not query:
            return []
        with self.connect() as db:
            rows = db.execute('SELECT source,location,text FROM chunks').fetchall()
        ranked = []
        for row in rows:
            overlap = query & tokens(row[2])
            if overlap:
                ranked.append((len(overlap) / len(query), row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for score, row in ranked[:4] if score >= .15]

    def ask(self, question, env, report_context=None):
        self.last_context = None
        if len(question) > 2000:
            return '问题过长，请缩短至2000字以内。'
        hits = self.search(question)
        if not hits and not report_context:
            return '知识库中没有找到足够相关的资料，请补充问题描述或联系管理员导入资料。'
        key = env.get('DEEPSEEK_API_KEY', '').strip()
        model = env.get('DEEPSEEK_MODEL', '').strip()
        if not key or not model:
            return '知识问答尚未配置完成，请管理员配置 DEEPSEEK_API_KEY 和 DEEPSEEK_MODEL。'
        base = env.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')
        if not base.startswith('https://'):
            return '知识问答接口必须使用HTTPS，请联系管理员。'
        context_title = '本地模型日报'
        if report_context:
            try:
                kind = json.loads(report_context).get('资料类型')
                if kind in ('今日小时采集', '今日半小时采集', '半小时监测预警', '新版利润日报'):
                    context_title = kind
            except (ValueError, TypeError, AttributeError):
                pass
        context = [{'编号': i+1, '文档': Path(row[0]).name, '位置': row[1], '内容': row[2]} for i, row in enumerate(hits)]
        if report_context:
            context.insert(0, {'编号': 1, '文档': context_title, '内容': report_context})
            for i, item in enumerate(context, 1):
                item['编号'] = i
        body = {'model': model, 'stream': False, 'max_tokens': 2000, 'thinking': {'type': 'disabled'}, 'messages': [
            {'role': 'system', 'content': BRIEFING_SYSTEM_PROMPT + ('\n本次为新版利润日报分析：以新版利润日报的policy与已计算金额为准，覆盖上文旧模型固定费率、毛利率和保本系数；不重算或套用旧价格档。数据日期按给定北京时间日期，不能把旧日期称为昨日。null保持待核验；可计算部分合计不能代表完整账户。反馈暂停，不要求投手反馈或操作机器人，不执行预算变更。' if context_title == '新版利润日报' else '')},
            {'role': 'user', 'content': json.dumps({'问题': question, '检索资料': context}, ensure_ascii=False)}]}
        request = urllib.request.Request(base + '/chat/completions', data=json.dumps(body).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
        try:
            from .reliability import retry_io
            def invoke():
                with urllib.request.urlopen(request, timeout=40) as response:
                    return json.load(response)
            result = retry_io(invoke, attempts=2)
            choice = result['choices'][0]
            text = choice['message'].get('content')
            if choice.get('finish_reason') == 'length' and not text:
                return '模型输出额度已耗尽，未生成回答正文，请联系管理员检查模型输出配置。'
            if choice.get('finish_reason') == 'length' and text:
                text += '\n（回答达到输出长度限制，内容可能不完整，请缩小问题范围。）'
            if not isinstance(text, str) or not text.strip():
                raise ValueError('empty answer')
        except Exception:
            # API异常可能包含请求头或服务商原文，不发往群或日志。
            return '知识问答服务暂时不可用，请稍后重试；报告与计划查询仍可使用。'
        self.last_context = json.dumps(context, ensure_ascii=False)
        if report_context:
            return ('依据今日已采集完整小时（不含正在进行的小时）及检索资料。\n' if context_title == '今日小时采集' else '依据累计快照及监测资料（以采集时间和范围为准）。\n' if context_title in ('今日半小时采集', '半小时监测预警') else '依据本地已保存日报（不代表实时结果）及检索资料。\n') + text[:4000] + '\n\n参考资料：\n' + '\n'.join('[%d] %s%s' % (item['编号'], item['文档'], ' · ' + item['位置'] if item.get('位置') else '') for item in context)
        return text[:4000] + '\n\n检索参考资料：\n' + '\n'.join('[%d] %s · %s' % (i+1, Path(row[0]).name, row[1]) for i, row in enumerate(hits))


def main():
    import sys
    sys.path.insert(0, str(BASE / 'vendor'))
    parser = argparse.ArgumentParser(description='本地知识库维护，不调用LLM、不发送飞书')
    parser.add_argument('action', choices=['import', 'list', 'delete', 'search'])
    parser.add_argument('value', nargs='?')
    parser.add_argument('--db', default=str(DEFAULT_DB))
    args = parser.parse_args()
    kb = Knowledge(args.db)
    if args.action in ('import', 'delete', 'search') and not args.value:
        parser.error('此操作需要文件路径或检索问题')
    if args.action == 'import':
        print('已导入 %d 个片段' % kb.ingest(args.value))
    elif args.action == 'search':
        for source, location, text in kb.search(args.value):
            print(Path(source).name, location, text)
    else:
        with kb.connect() as db:
            if args.action == 'delete':
                db.execute('DELETE FROM chunks WHERE source=?', (str(Path(args.value).resolve()),))
                print('已移除该文件的知识索引')
            else:
                for row in db.execute('SELECT source,count(*) FROM chunks GROUP BY source'):
                    print(*row)


if __name__ == '__main__':
    main()
