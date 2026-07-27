import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update the search bar container to include the button
old_search = """                <div style="width: 250px;" class="suggestions-container">
                    <div class="input-group input-group-sm">
                        <span class="input-group-text bg-white border-end-0"><i class="bi bi-search">🔍</i></span>"""
new_search = """                <div class="d-flex align-items-center gap-2">
                    <button class="btn btn-sm btn-light border text-secondary" id="toggleEditBtn" onclick="toggleEditColumns()" title="Modo Edición">⚙️</button>
                    <div style="width: 250px;" class="suggestions-container">
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0"><i class="bi bi-search">🔍</i></span>"""
content = content.replace(old_search, new_search)

# fix the closing div of the search bar container
old_search_close = """                    <div id="filter-search-suggestions" class="search-suggestions"></div>
                </div>
            </div>"""
new_search_close = """                    <div id="filter-search-suggestions" class="search-suggestions"></div>
                    </div>
                </div>
            </div>"""
content = content.replace(old_search_close, new_search_close)

# 2. Update the `th` elements. Replace the ⚙️ header with the new edit column header
old_th = """<th class="text-center border-start border-5 border-white bg-light shadow-sm" style="width: 80px;">⚙️</th>"""
new_th = """<th class="text-center border-start border-5 border-white bg-light shadow-sm edit-column" style="width: 80px; display: none;">Acciones</th>"""
content = content.replace(old_th, new_th)

# 3. Update the `td` elements.
old_td = """<td class="text-center border-start border-5 border-white bg-light" onclick="event.stopPropagation();">"""
new_td = """<td class="text-center border-start border-5 border-white bg-light edit-column" style="display: none;" onclick="event.stopPropagation();">"""
content = content.replace(old_td, new_td)

# 4. Add the toggleEditColumns function to the script tag
script_inject = """<script>
    function toggleEditColumns() {
        const columns = document.querySelectorAll('.edit-column');
        const btn = document.getElementById('toggleEditBtn');
        let isHidden = true;
        if (columns.length > 0) {
            isHidden = columns[0].style.display === 'none';
        }
        
        columns.forEach(col => {
            if (isHidden) {
                col.style.display = 'table-cell';
            } else {
                col.style.display = 'none';
            }
        });
        
        if (isHidden) {
            btn.classList.add('bg-secondary', 'text-white');
            btn.classList.remove('bg-light', 'text-secondary');
        } else {
            btn.classList.remove('bg-secondary', 'text-white');
            btn.classList.add('bg-light', 'text-secondary');
        }
    }
"""
content = content.replace("<script>", script_inject)

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
