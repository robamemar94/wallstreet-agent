import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# 1. Update price formatting to single line
price_block_old = r'''<td class="text-center" data-sort-value="\{\{ t\.market_data\.pct \}\}">
                                <div class="fw-bold text-dark">\$\{\{ "\{:,\.2f\}".format\(t\.market_data\.price\) \}\}</div>
                                <div class="small fw-bold \{\% if t\.market_data\.change > 0 \%\}text-success\{\% elif t\.market_data\.change < 0 \%\}text-danger\{\% else \%\}text-muted\{\% endif \%\}">
                                    \{\% if t\.market_data\.change > 0 \%\}\+\{\% endif \%\}\{\{ "\{:,\.2f\}".format\(t\.market_data\.change\) \}\} 
                                    \(\{\% if t\.market_data\.pct > 0 \%\}\+\{\% endif \%\}\{\{ "\{:\.2f\}%".format\(t\.market_data\.pct\) \}\}\)
                                </div>
                            </td>'''

price_block_new = '''<td class="text-center" data-sort-value="{{ t.market_data.pct }}">
                                <span class="fw-bold text-dark">${{ "{:,.2f}".format(t.market_data.price) }}</span>
                                <span class="small fw-bold ms-1 {% if t.market_data.change > 0 %}text-success{% elif t.market_data.change < 0 %}text-danger{% else %}text-muted{% endif %}">
                                    {% if t.market_data.change > 0 %}+{% endif %}{{ "{:,.2f}".format(t.market_data.change) }} ({% if t.market_data.pct > 0 %}+{% endif %}{{ "{:.2f}%".format(t.market_data.pct) }})
                                </span>
                            </td>'''

content = re.sub(price_block_old, price_block_new, content)

# 2. Update scores to remove /10. It currently prints {{ t.scores.fin }}, etc.
# We will use Jinja2 to split and take the first part: {{ t.scores.fin.split('/')[0] }} if t.scores.fin != '-' else '-'
score_replacements = ['fin', 'cap', 'moat', 'verdict']
for s in score_replacements:
    old_val = f"{{{{ t.scores.{s} }}}}"
    new_val = f"{{{{ t.scores.{s}.split('/')[0] if t.scores.{s} and t.scores.{s} != '-' else '-' }}}}"
    content = content.replace(old_val, new_val)

# 3. Add STANDBY table
# Let's clone pending table block
start_pending = content.find('<!-- Pendientes -->')
end_pending = content.find('<!-- Descartadas -->')
pending_block = content[start_pending:end_pending]

standby_block = pending_block.replace(
    '<!-- Pendientes -->', '<!-- En Observación / Dudas -->'
).replace(
    'Expedientes en Análisis / Pendientes', 'Expedientes en Observación / Dudas'
).replace(
    'Empresas pendientes de decisión', 'Empresas analizadas con decisión pendiente (Standby)'
).replace(
    'id="pendingTable"', 'id="standbyTable"'
).replace(
    'id="pendingBody"', 'id="standbyBody"'
).replace(
    "'pendingTable'", "'standbyTable'"
).replace(
    '"pendingTable"', '"standbyTable"'
).replace(
    "t.status == 'PENDING'", "t.status == 'STANDBY'"
).replace(
    'id="pendingInfo"', 'id="standbyInfo"'
).replace(
    'id="pendingPagination"', 'id="standbyPagination"'
)

# Insert standby_block before rejected
content = content[:end_pending] + standby_block + content[end_pending:]

# Update pagination array logic
content = content.replace(
    "'pendingTable': 1,",
    "'pendingTable': 1,\n                    'standbyTable': 1,"
)
content = content.replace(
    "updatePagination('pendingTable');",
    "updatePagination('pendingTable');\n                    updatePagination('standbyTable');"
)
content = content.replace(
    "currentPages['pendingTable'] = 1;",
    "currentPages['pendingTable'] = 1;\n                    currentPages['standbyTable'] = 1;"
)

# 4. Filter status dropdown: add Dudas
content = content.replace('<option value="REJECTED">❌ Descartadas</option>', '<option value="STANDBY">🤔 Dudas</option>\n                                <option value="REJECTED">❌ Descartadas</option>')

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated basic HTML layout")
