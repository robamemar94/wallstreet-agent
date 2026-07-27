import re

with open('templates/index.html', 'r') as f:
    content = f.read()

for table_id in ['acceptedTable', 'pendingTable', 'standbyTable', 'rejectedTable']:
    # The actual string in the file is:
    # <td class="text-center delete-col-acceptedTable" style="display: none;">
    #                             <button class="btn btn-sm btn-outline-danger border-0" onclick="event.stopPropagation(); deleteTicker(\'{{ t.name }}\');" title="Eliminar {{ t.name }}">🗑️</button>
    #                         </td>
    
    old_pattern = r'<td class="text-center delete-col-' + table_id + r'" style="display: none;">\s*<button class="btn btn-sm btn-outline-danger border-0" onclick="event\.stopPropagation\(\); deleteTicker\(\\\'\{\{ t\.name \}\}\\\'\);" title="Eliminar \{\{ t\.name \}\}">🗑️</button>\s*</td>'
    
    # Try another pattern if the above fails (without backslash escaping of quotes)
    old_pattern_2 = r'<td class="text-center delete-col-' + table_id + r'" style="display: none;">\s*<button class="btn btn-sm btn-outline-danger border-0" onclick="event\.stopPropagation\(\); deleteTicker\(\'\{\{ t\.name \}\}\'\);" title="Eliminar \{\{ t\.name \}\}">🗑️</button>\s*</td>'

    new_cell = f'''<td class="text-center delete-col-{table_id}" style="display: none;">
                                <div class="btn-group btn-group-sm" role="group">
                                    <button class="btn btn-outline-success border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'ACCEPTED');" title="Aceptar">✅</button>
                                    <button class="btn btn-outline-secondary border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'PENDING');" title="Pendiente">⏳</button>
                                    <button class="btn btn-outline-warning text-dark border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'STANDBY');" title="Dudas">🤔</button>
                                    <button class="btn btn-outline-danger border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'REJECTED');" title="Descartar">❌</button>
                                    <button class="btn btn-outline-dark border-0 px-1 ms-2" onclick="event.stopPropagation(); deleteTicker('{{{{ t.name }}}}');" title="Eliminar {{{{ t.name }}}}">🗑️</button>
                                </div>
                            </td>'''
    
    content = re.sub(old_pattern, new_cell, content)
    content = re.sub(old_pattern_2, new_cell, content)

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Done")
