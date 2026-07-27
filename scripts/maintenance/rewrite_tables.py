import re

with open('templates/index.html', 'r') as f:
    content = f.read()

start_active = content.find('<!-- Activas y Pendientes -->')
end_active = content.find('<!-- Descartadas -->')

active_block = content[start_active:end_active]

accepted_block = active_block.replace(
    '<!-- Activas y Pendientes -->', '<!-- Aceptadas -->'
).replace(
    'Expedientes Activos', 'Expedientes Aceptados / Alta Vigilancia'
).replace(
    'Empresas en seguimiento o pendientes', 'Empresas en seguimiento prioritario'
).replace(
    'id="activeTable"', 'id="acceptedTable"'
).replace(
    'id="activeBody"', 'id="acceptedBody"'
).replace(
    "'activeTable'", "'acceptedTable'"
).replace(
    '"activeTable"', '"acceptedTable"'
).replace(
    "t.status != 'REJECTED'", "t.status == 'ACCEPTED'"
).replace(
    'id="activeInfo"', 'id="acceptedInfo"'
).replace(
    'id="activePagination"', 'id="acceptedPagination"'
)

pending_block = active_block.replace(
    '<!-- Activas y Pendientes -->', '<!-- Pendientes -->'
).replace(
    'Expedientes Activos', 'Expedientes en Análisis / Pendientes'
).replace(
    'Empresas en seguimiento o pendientes', 'Empresas pendientes de decisión'
).replace(
    'id="activeTable"', 'id="pendingTable"'
).replace(
    'id="activeBody"', 'id="pendingBody"'
).replace(
    "'activeTable'", "'pendingTable'"
).replace(
    '"activeTable"', '"pendingTable"'
).replace(
    "t.status != 'REJECTED'", "t.status == 'PENDING'"
).replace(
    'id="activeInfo"', 'id="pendingInfo"'
).replace(
    'id="activePagination"', 'id="pendingPagination"'
)

# Remove the search input from pending block
search_html = """                <div style="width: 250px;">
                    <div class="input-group input-group-sm">
                        <span class="input-group-text bg-white border-end-0"><i class="bi bi-search">🔍</i></span>
                        <input type="text" class="form-control border-start-0 filter-input" id="filter-search" placeholder="Buscar Ticker o Nombre...">
                    </div>
                </div>"""
pending_block = pending_block.replace(search_html, '')

content = content[:start_active] + accepted_block + pending_block + content[end_active:]

content = content.replace("currentPages['activeTable'] = 1;", "currentPages['acceptedTable'] = 1;\n                    currentPages['pendingTable'] = 1;")
content = content.replace("updatePagination('activeTable');", "updatePagination('acceptedTable');\n                    updatePagination('pendingTable');")
content = content.replace("'activeTable': 1,", "'acceptedTable': 1,\n                    'pendingTable': 1,")

content = content.replace(
    "const infoId = tableId === 'activeTable' ? 'activeInfo' : 'rejectedInfo';",
    "const infoId = tableId.replace('Table', 'Info');"
)
content = content.replace(
    "const paginationId = tableId === 'activeTable' ? 'activePagination' : 'rejectedPagination';",
    "const paginationId = tableId.replace('Table', 'Pagination');"
)

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated index.html")
