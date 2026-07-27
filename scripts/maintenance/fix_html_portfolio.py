import re

with open('templates/portfolio.html', 'r') as f:
    content = f.read()

# 1. Remove the edit button from the active table
old_btns = """                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-primary ms-1 px-2 py-0" onclick="editPosition('{{ item.ticker }}', {{ item.shares }}, {{ item.average_price }})" title="Editar">✏️</button>
                        <button class="btn btn-sm btn-outline-danger ms-1 px-2 py-0" onclick="deletePosition('{{ item.ticker }}')" title="Eliminar">🗑️</button>
                    </td>"""
new_btns = """                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-danger ms-1 px-2 py-0" onclick="deletePosition('{{ item.ticker }}')" title="Eliminar Todo el Histórico">🗑️</button>
                    </td>"""
content = content.replace(old_btns, new_btns)

# 2. Remove Modal and associated JS
modal_start = content.find('<!-- Modal para Editar Posición -->')
modal_end = content.find('<script>')
if modal_start != -1 and modal_end != -1:
    content = content[:modal_start] + content[modal_end:]

js_remove_start = content.find('let editModal;')
js_remove_end = content.find('</script>', js_remove_start)

if js_remove_start != -1 and js_remove_end != -1:
    content = content[:js_remove_start] + content[js_remove_end:]

with open('templates/portfolio.html', 'w') as f:
    f.write(content)
print("Removed edit modal")
