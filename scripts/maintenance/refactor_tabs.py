import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# Locate the blocks
start_acc = content.find('<!-- Aceptadas -->')
start_pen = content.find('<!-- Pendientes -->')
start_std = content.find('<!-- En Observación / Dudas -->')
start_rej = content.find('<!-- Descartadas -->')
end_all = content.find('<script>', start_rej)

if -1 in [start_acc, start_pen, start_std, start_rej, end_all]:
    print("Could not find blocks")
    exit(1)

# Extract search bar from accepted block
search_bar = """<div style="width: 250px;">
                    <div class="input-group input-group-sm">
                        <span class="input-group-text bg-white border-end-0"><i class="bi bi-search">🔍</i></span>
                        <input type="text" class="form-control border-start-0 filter-input" id="filter-search" placeholder="Buscar Ticker o Nombre...">
                    </div>
                </div>"""

# Remove search bar from the accepted block content temporarily
acc_block = content[start_acc:start_pen]
acc_block = acc_block.replace(search_bar, '')
pen_block = content[start_pen:start_std]
std_block = content[start_std:start_rej]
rej_block = content[start_rej:end_all]

# Construct tabs layout
tabs_html = f"""
            <!-- Search bar & Tabs Header -->
            <div class="d-flex justify-content-between align-items-end mb-3">
                <ul class="nav nav-tabs border-bottom-0" id="portfolioTabs" role="tablist">
                    <li class="nav-item" role="presentation">
                        <button class="nav-link active fw-bold text-success bg-white" id="accepted-tab" data-bs-toggle="tab" data-bs-target="#accepted-pane" type="button" role="tab" aria-controls="accepted-pane" aria-selected="true">✅ Alta Vigilancia</button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link fw-bold text-secondary bg-light" id="pending-tab" data-bs-toggle="tab" data-bs-target="#pending-pane" type="button" role="tab" aria-controls="pending-pane" aria-selected="false">⏳ Pendientes</button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link fw-bold text-warning text-dark bg-light" id="standby-tab" data-bs-toggle="tab" data-bs-target="#standby-pane" type="button" role="tab" aria-controls="standby-pane" aria-selected="false">🤔 Dudas</button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link fw-bold text-danger bg-light" id="rejected-tab" data-bs-toggle="tab" data-bs-target="#rejected-pane" type="button" role="tab" aria-controls="rejected-pane" aria-selected="false">❌ Descartadas</button>
                    </li>
                </ul>
                {search_bar}
            </div>
            
            <div class="tab-content border border-top-0 rounded-bottom bg-white shadow-sm mb-5 p-3" id="portfolioTabsContent">
                <!-- Tab Pane Aceptadas -->
                <div class="tab-pane fade show active" id="accepted-pane" role="tabpanel" aria-labelledby="accepted-tab" tabindex="0">
                    {acc_block}
                </div>
                <!-- Tab Pane Pendientes -->
                <div class="tab-pane fade" id="pending-pane" role="tabpanel" aria-labelledby="pending-tab" tabindex="0">
                    {pen_block}
                </div>
                <!-- Tab Pane Dudas -->
                <div class="tab-pane fade" id="standby-pane" role="tabpanel" aria-labelledby="standby-tab" tabindex="0">
                    {std_block}
                </div>
                <!-- Tab Pane Descartadas -->
                <div class="tab-pane fade" id="rejected-pane" role="tabpanel" aria-labelledby="rejected-tab" tabindex="0">
                    {rej_block}
                </div>
            </div>
"""

# Now we need to remove the top/bottom shadows and margins from the table containers
# inside the tabs to make it look clean.
tabs_html = tabs_html.replace('bg-white rounded shadow-sm mb-5', '')
tabs_html = tabs_html.replace('mb-3 mt-5', 'mb-3')
tabs_html = tabs_html.replace('<hr>', '')

# Assemble final content
new_content = content[:start_acc] + tabs_html + content[end_all:]

# Add JS to make tabs visually distinct when inactive (bg-light to bg-white)
js_tab_colors = """
                // Tab styling
                document.querySelectorAll('#portfolioTabs button[data-bs-toggle="tab"]').forEach(tab => {
                    tab.addEventListener('shown.bs.tab', event => {
                        document.querySelectorAll('#portfolioTabs button').forEach(btn => {
                            btn.classList.remove('bg-white');
                            btn.classList.add('bg-light');
                        });
                        event.target.classList.remove('bg-light');
                        event.target.classList.add('bg-white');
                    });
                });
"""
if "Tab styling" not in new_content:
    new_content = new_content.replace('function applyFilters() {', js_tab_colors + '\n                function applyFilters() {')


with open('templates/index.html', 'w') as f:
    f.write(new_content)
print("Done")
