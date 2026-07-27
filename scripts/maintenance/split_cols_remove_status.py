import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# 1. Remove "Estado" column entirely from all tables
# In pendingTable and standbyTable, we need to remove the header and the cell.
# Let's find all headers with "Estado"
content = re.sub(r'<th class="text-center" onclick="sortTable\([^,]+, 5\)">Estado ↕</th>\n', '', content)

# Remove the td with data-sort-value="{{ t.status }}"
# Wait, let's be precise.
content = re.sub(r'\s*<td class="text-center" data-sort-value="\{\{ t\.status \}\}">\s*(?:<span.*?>.*?</span>|.*?)\s*</td>', '', content, flags=re.DOTALL)


# 2. Split "Precio / Var." into two columns: "Precio" and "Var."
# Headers:
# Find: <th class="text-center" onclick="sortTable('acceptedTable', 4)">Precio / Var. ↕</th>
# Replace with: <th class="text-center" onclick="sortTable('acceptedTable', 4)">Precio ↕</th>\n<th class="text-center" onclick="sortTable('acceptedTable', 5)">Var. ↕</th>

# We have to fix the column indices for sorting!
# Right now, in acceptedTable:
# 0: Ticker, 1: Empresa, 2: País, 3: Sector, 4: Precio / Var, 5: Fin, 6: Cap, 7: Moat, 8: Verdict, 9: Val, 10: ⚙️ (because we removed 5: Estado)
# Wait, did we remove 5: Estado in update_cols.py? Yes, for accepted and rejected.
# For pending and standby, 5 was Estado, 6: Fin...
# If we remove Estado from pending/standby, their indices will match accepted/rejected.
# Then we add a new column for Var (making it 5). Then Fin becomes 6 again.
# This means indices after 4 need to be shifted +1 for accepted/rejected, and for pending/standby they stay the same?
# Actually, the easiest way is to rewrite the headers and columns explicitly for all 4 tables.

def rewrite_table(table_id, block):
    # Fix headers
    headers = [
        ('Ticker', 'ps-4', False),
        ('Empresa', 'ps-2', False),
        ('País', 'ps-2', False),
        ('Sector', 'ps-2', False),
        ('Precio', 'text-center', False),
        ('Var.', 'text-center', False),
        ('Financiero', 'text-center', True),
        ('Capital', 'text-center', True),
        ('Moat', 'text-center', True),
        ('Veredicto', 'text-center', True),
        ('Valoración', 'text-center', False)
    ]
    
    # We will just replace the whole <thead>
    thead_start = block.find('<thead class="table-light">')
    thead_end = block.find('</thead>') + len('</thead>')
    
    new_thead = '<thead class="table-light">\n                        <tr style="cursor: pointer;" title="Haz clic en una cabecera para ordenar">\n'
    for i, (name, cls, is_num) in enumerate(headers):
        num_arg = ', true' if is_num else ''
        new_thead += f'                            <th class="{cls}" onclick="sortTable(\'{table_id}\', {i}{num_arg})">{name} ↕</th>\n'
    new_thead += f'                            <th class="text-center" onclick="toggleDeleteButtons(\'{table_id}\')" title="Mostrar/Ocultar Borrar" style="cursor: pointer;">⚙️</th>\n'
    new_thead += '                        </tr>\n                    </thead>'
    
    block = block[:thead_start] + new_thead + block[thead_end:]
    
    # Now fix the body columns
    # Split the Price / Var cell
    price_var_cell_regex = r'<td class="text-center" data-sort-value="\{\{ t\.market_data\.pct \}\}">\s*<span class="fw-bold text-dark">\$\{\{ "\{:,\.2f\}".format\(t\.market_data\.price\) \}\}<\/span>\s*<span class="small fw-bold ms-1 \{% if t\.market_data\.change > 0 %\}text-success\{% elif t\.market_data\.change < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}">\s*\{% if t\.market_data\.change > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(t\.market_data\.change\) \}\} \(\{% if t\.market_data\.pct > 0 %\}\+\{% endif %\}\{\{ "\{:\.2f\}%".format\(t\.market_data\.pct\) \}\}\)\s*<\/span>\s*<\/td>'
    
    new_price_var_cells = '''<td class="text-center fw-bold text-dark" data-sort-value="{{ t.market_data.price }}">${{ "{:,.2f}".format(t.market_data.price) }}</td>
                            <td class="text-center fw-bold {% if t.market_data.change > 0 %}text-success{% elif t.market_data.change < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ t.market_data.pct }}">
                                {% if t.market_data.change > 0 %}+{% endif %}{{ "{:,.2f}".format(t.market_data.change) }} ({% if t.market_data.pct > 0 %}+{% endif %}{{ "{:.2f}%".format(t.market_data.pct) }})
                            </td>'''
                            
    block = re.sub(price_var_cell_regex, new_price_var_cells, block)
    
    # Update delete button cell to have a class we can toggle, and hide by default
    del_btn_regex = r'<td class="text-center">\s*<button class="btn btn-sm btn-outline-danger border-0" onclick="event\.stopPropagation\(\); deleteTicker\(\'\{\{ t\.name \}\}\'\);" title="Eliminar \{\{ t\.name \}\}">🗑️<\/button>\s*<\/td>'
    new_del_btn = r'<td class="text-center delete-col-' + table_id + r'" style="display: none;">\n                                <button class="btn btn-sm btn-outline-danger border-0" onclick="event.stopPropagation(); deleteTicker(\'{{ t.name }}\');" title="Eliminar {{ t.name }}">🗑️</button>\n                            </td>'
    
    block = re.sub(del_btn_regex, new_del_btn, block)
    
    return block


for t_id in ['acceptedTable', 'pendingTable', 'standbyTable', 'rejectedTable']:
    start_idx = content.find(f'id="{t_id}"')
    if start_idx != -1:
        # expand to table block
        table_start = content.rfind('<table', 0, start_idx)
        table_end = content.find('</table>', start_idx) + len('</table>')
        
        block = content[table_start:table_end]
        new_block = rewrite_table(t_id, block)
        content = content[:table_start] + new_block + content[table_end:]


# Add JS for toggling delete buttons
js_toggle = """
                function toggleDeleteButtons(tableId) {
                    const cols = document.querySelectorAll('.delete-col-' + tableId);
                    cols.forEach(col => {
                        if (col.style.display === 'none') {
                            col.style.display = '';
                        } else {
                            col.style.display = 'none';
                        }
                    });
                }
"""

if 'function toggleDeleteButtons' not in content:
    content = content.replace('function applyFilters() {', js_toggle + '\n                function applyFilters() {')


with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated columns and toggles")
