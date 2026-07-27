import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# 1. Update Headers: Merge Precio and Var. into "Precio / Var."
# and shift subsequent indices back.
def fix_headers(block, table_id):
    # Old headers:
    # <th class="ps-4" onclick="sortTable('acceptedTable', 0)">Ticker ↕</th>
    # <th class="ps-2" onclick="sortTable('acceptedTable', 1)">Empresa ↕</th>
    # <th class="ps-2" onclick="sortTable('acceptedTable', 2)">País ↕</th>
    # <th class="ps-2" onclick="sortTable('acceptedTable', 3)">Sector ↕</th>
    # <th class="text-center" onclick="sortTable('acceptedTable', 4)">Precio ↕</th>
    # <th class="text-center" onclick="sortTable('acceptedTable', 5)">Var. ↕</th>
    # <th class="text-center" onclick="sortTable('acceptedTable', 6, true)">Financiero ↕</th>
    # ...
    
    # We want:
    # 4: Precio / Var.
    # 5: Financiero (shift 6->5, 7->6, etc.)
    
    thead_start = block.find('<thead class="table-light">')
    thead_end = block.find('</thead>') + len('</thead>')
    
    new_thead = f'''<thead class="table-light">
                        <tr style="cursor: pointer;" title="Haz clic en una cabecera para ordenar">
                            <th class="ps-4" onclick="sortTable('{table_id}', 0)">Ticker ↕</th>
                            <th class="ps-2" onclick="sortTable('{table_id}', 1)">Empresa ↕</th>
                            <th class="ps-2" onclick="sortTable('{table_id}', 2)">País ↕</th>
                            <th class="ps-2" onclick="sortTable('{table_id}', 3)">Sector ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 4)">Precio / Var. ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 5, true)">Financiero ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 6, true)">Capital ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 7, true)">Moat ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 8, true)">Veredicto ↕</th>
                            <th class="text-center" onclick="sortTable('{table_id}', 9)">Valoración ↕</th>
                            <th class="text-center" onclick="toggleDeleteButtons('{table_id}')" title="Mostrar/Ocultar Borrar" style="cursor: pointer;">⚙️</th>
                        </tr>
                    </thead>'''
    
    return block[:thead_start] + new_thead + block[thead_end:]

# 2. Update Body Rows: Merge the two cells into one with vertical layout
def fix_body(block, table_id):
    # Find the two cells:
    # <td class="text-center fw-bold text-dark" data-sort-value="{{ t.market_data.price }}">${{ "{:,.2f}".format(t.market_data.price) }}</td>
    # <td class="text-center fw-bold {% if t.market_data.change > 0 %}text-success{% elif t.market_data.change < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ t.market_data.pct }}">
    #     {% if t.market_data.change > 0 %}+{% endif %}{{ "{:,.2f}".format(t.market_data.change) }} ({% if t.market_data.pct > 0 %}+{% endif %}{{ "{:.2f}%".format(t.market_data.pct) }})
    # </td>
    
    # Note: Use t.currency_symbol instead of $
    
    cell_pattern = r'<td class="text-center fw-bold text-dark" data-sort-value="\{\{ t\.market_data\.price \}\}">\$\{\{ "\{:,\.2f\}".format\(t\.market_data\.price\) \}\}</td>\s*<td class="text-center fw-bold \{% if t\.market_data\.change > 0 %\}text-success\{% elif t\.market_data\.change < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}" data-sort-value="\{\{ t\.market_data\.pct \}\}">\s*\{% if t\.market_data\.change > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(t\.market_data\.change\) \}\} \(\{% if t\.market_data\.pct > 0 %\}\+\{% endif %\}\{\{ "\{:\.2f\}%".format\(t\.market_data\.pct\) \}\}\)\s*</td>'
    
    new_cell = r'''<td class="text-center" data-sort-value="{{ t.market_data.pct }}">
                                <div class="fw-bold text-dark">{{ t.currency_symbol }}{{ "{:,.2f}".format(t.market_data.price) }}</div>
                                <div class="small fw-bold {% if t.market_data.change > 0 %}text-success{% elif t.market_data.change < 0 %}text-danger{% else %}text-muted{% endif %}">
                                    {% if t.market_data.change > 0 %}+{% endif %}{{ "{:,.2f}".format(t.market_data.change) }} 
                                    ({% if t.market_data.pct > 0 %}+{% endif %}{{ "{:.2f}%".format(t.market_data.pct) }})
                                </div>
                            </td>'''
    
    return re.sub(cell_pattern, new_cell, block)

for t_id in ['acceptedTable', 'pendingTable', 'standbyTable', 'rejectedTable']:
    start_idx = content.find(f'id="{t_id}"')
    if start_idx != -1:
        table_start = content.rfind('<table', 0, start_idx)
        table_end = content.find('</table>', start_idx) + len('</table>')
        block = content[table_start:table_end]
        
        block = fix_headers(block, t_id)
        block = fix_body(block, t_id)
        
        content = content[:table_start] + block + content[table_end:]

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Reverted price layout and added currency symbols")
