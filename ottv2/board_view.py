"""Small SVG of the published starting position; no browser libraries."""

from html import escape


def render(state, name):
    colors = {'A': '#b6423b', 'B': '#28628e'}
    hands = {'rock': '✊', 'paper': '✋', 'scissors': '✌'}
    labels = {'rock': 'Búa', 'paper': 'Bao', 'scissors': 'Kéo'}
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 570" role="img">',
           '<title>'+escape('Bàn cờ ban đầu: '+name)+'</title>',
           '<rect width="540" height="570" fill="#ffffff"/>']
    for row in range(9):
        for col in range(9):
            pos = chr(97+col)+str(9-row)
            x,y=36+col*52,20+row*52
            fill = '#adc8e8' if (row+col)%2 else '#e8f0fa'
            svg.append(f'<rect x="{x}" y="{y}" width="52" height="52" fill="{fill}"/>')
            if pos in state['obstacles']:
                svg.append(f'<rect x="{x+8}" y="{y+8}" width="36" height="36" rx="4" fill="#7a879d"><title>Chướng ngại: {pos}</title></rect>')
            if pos in state['bombs']:
                svg.append(f'<circle cx="{x+26}" cy="{y+29}" r="12" fill="#303a34"><title>Bom: {pos}</title></circle>'
                           f'<path d="M{x+29} {y+17}l5 -8 6 4" stroke="#ad722c" stroke-width="4" fill="none"/>')
            for side in ('A','B'):
                if state['goals'][side] == pos:
                    svg.append(f'<rect x="{x+3}" y="{y+3}" width="46" height="46" fill="none" stroke="{colors[side]}" stroke-width="3"><title>Đích của {side}: {pos}</title></rect>')
            piece = state['board'].get(pos)
            if piece:
                side,kind=piece['side'],piece['kind']
                svg.append(f'<g><title>{pos}: {labels[kind]} bên {side}</title>'
                           f'<circle cx="{x+26}" cy="{y+26}" r="20" fill="{colors[side]}"/>'
                           f'<text x="{x+26}" y="{y+36}" text-anchor="middle" font-size="30" font-family="Segoe UI Emoji,Noto Color Emoji,sans-serif">{hands[kind]}</text></g>')
    for n in range(9):
        svg.append(f'<text x="{62+n*52}" y="510" text-anchor="middle" font-size="15" fill="#59665b">{chr(97+n)}</text>'
                   f'<text x="20" y="{52+n*52}" text-anchor="middle" font-size="15" fill="#59665b">{9-n}</text>')
    svg.append('<text x="38" y="545" font-family="sans-serif" font-size="15" fill="#b6423b">● A đỏ</text>'
               '<text x="160" y="545" font-family="sans-serif" font-size="15" fill="#28628e">● B xanh</text>'
               '<text x="282" y="545" font-family="sans-serif" font-size="13" fill="#59665b">Ô viền: đích của mỗi bên</text></svg>')
    return ''.join(svg).encode('utf-8')
