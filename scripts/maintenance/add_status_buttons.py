import re

with open('templates/index.html', 'r') as f:
    content = f.read()

for table_id in ['acceptedTable', 'pendingTable', 'standbyTable', 'rejectedTable']:
    old_cell = (
        r'<td class="text-center delete-col-' + table_id + r'" style="display: none;">\s*'
        r'<button class="btn btn-sm btn-outline-danger border-0" onclick="event\.stopPropagation\(\); deleteTicker\(\'\{\{ t\.name \}\}\'\);" title="Eliminar \{\{ t\.name \}\}">🗑️</button>\s*'
        r'</td>'
    )
    
    new_cell = f'''<td class="text-center delete-col-{table_id}" style="display: none;">
                                <div class="btn-group btn-group-sm" role="group">
                                    <button class="btn btn-outline-success border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'ACCEPTED');" title="Aceptar">✅</button>
                                    <button class="btn btn-outline-secondary border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'PENDING');" title="Pendiente">⏳</button>
                                    <button class="btn btn-outline-warning text-dark border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'STANDBY');" title="Dudas">🤔</button>
                                    <button class="btn btn-outline-danger border-0 px-1" onclick="event.stopPropagation(); updateStatusIndex('{{{{ t.name }}}}', 'REJECTED');" title="Descartar">❌</button>
                                    <button class="btn btn-outline-dark border-0 px-1 ms-2" onclick="event.stopPropagation(); deleteTicker('{{{{ t.name }}}}');" title="Eliminar {{{{ t.name }}}}">🗑️</button>
                                </div>
                            </td>'''
    
    content = re.sub(old_cell, new_cell, content)

js_func = """
                async function updateStatusIndex(ticker, status) {
                    try {
                        const response = await fetch(`/api/status/${ticker}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({ status: status })
                        });
                        const data = await response.json();
                        
                        if (data.status === 'success') {
                            if (typeof showToast === 'function') {
                                showToast('✅ Estado Actualizado', `El estado de ${ticker} ha cambiado a ${status}.`, 'success');
                            }
                            setTimeout(() => location.reload(), 500);
                        } else {
                            const errorMsg = data.message || data.detail || "Error desconocido";
                            alert("Error actualizando el estado: " + errorMsg);
                        }
                    } catch (err) {
                        alert("Error de conexión al actualizar el estado.");
                    }
                }
"""

if 'async function updateStatusIndex' not in content:
    content = content.replace('async function deleteTicker(ticker) {', js_func + '\n                async function deleteTicker(ticker) {')

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated index.html successfully")
