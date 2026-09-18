"""Embed the reviewed backlog in existing local Markdown/HTML plan artifacts."""
from pathlib import Path
import html
import re

root = Path(__file__).resolve().parents[1]
source = (root / 'docs/optimization-backlog.md').read_text(encoding='utf-8')


def inline(text):
    escaped = re.sub(r'`([^`]+)`', r'<code>\1</code>', html.escape(text))
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', escaped)


def render(text):
    result, table, listing, section = [], False, False, False
    def close_blocks():
        nonlocal table, listing
        if table:
            result.append('</tbody></table></div>')
            table = False
        if listing:
            result.append('</ul>')
            listing = False
    for line in text.splitlines():
        if line.startswith('|'):
            if re.fullmatch(r'[| :\-]+', line):
                continue
            if not table:
                close_blocks()
                result.append('<div style="overflow:auto"><table><tbody>')
                table = True
            result.append('<tr>'+''.join('<td>'+inline(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
        elif line.startswith('- ') or re.match(r'^\d+\. ', line):
            if not listing:
                close_blocks()
                result.append('<ul>')
                listing = True
            result.append('<li>'+inline(re.sub(r'^(?:- |\d+\. )','',line))+'</li>')
        else:
            close_blocks()
            if line.startswith('## '):
                if section: result.append('</div></details>')
                result.append('<details class="node" open><summary>'+inline(line[3:])+'</summary><div class="inside">')
                section = True
            elif line.startswith('# '):
                result.append('<h2>'+inline(line[2:])+'</h2>')
            elif line:
                result.append('<p>'+inline(line)+'</p>')
    close_blocks()
    if section: result.append('</div></details>')
    return ''.join(result)


for extension in ('md', 'html'):
    path = root / f'output/ui-search-optimization-plan.{extension}'
    content = path.read_text(encoding='utf-8')
    content = re.sub(r'<!-- backlog-start -->.*?<!-- backlog-end -->\s*', '', content, flags=re.S)
    content = content.replace('ĐANG TRIỂN KHAI / 21 MỤC CÒN MỞ', 'ĐANG TRIỂN KHAI / 18 MỤC QUA LOCAL / 4 CẦN NGHIỆM THU')
    content = content.replace('ĐỀ XUẤT ĐỂ DUYỆT / 22 HẠNG MỤC', 'ĐANG TRIỂN KHAI / 18 MỤC QUA LOCAL / 4 CẦN NGHIỆM THU')
    content = content.replace('Trạng thái: bản kế hoạch đề xuất để duyệt, ngày 17/09/2026. Chưa triển khai thay đổi ứng dụng.', 'Trạng thái: đang triển khai từ 17/09/2026; xem bảng cập nhật trước lộ trình gốc.')
    if extension == 'md':
        block = '\n<!-- backlog-start -->\n'+source.replace('# Tình trạng', '## Tình trạng', 1)+'\n<!-- backlog-end -->\n'
        first, rest = content.split('\n', 1)
        content = first+'\n'+block+rest
    else:
        block = '<!-- backlog-start --><style>#current-backlog td:first-child{white-space:nowrap;min-width:60px}#current-backlog td:nth-child(2){min-width:160px}#current-backlog td{vertical-align:top}</style><details class="node" open id="current-backlog"><summary>Cập nhật triển khai / 18 mục qua local / 4 cần nghiệm thu</summary><div class="inside">'+render(source)+'</div></details><!-- backlog-end -->'
        content = content.replace('</header>', '</header>'+block, 1)
    path.write_text(content, encoding='utf-8')
print('Updated both plan artifacts from docs/optimization-backlog.md')
