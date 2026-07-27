                // Paginación
                const itemsPerPage = 10;
                let currentPages = {
                    'activeTable': 1,
                    'rejectedTable': 1
                };

                function updatePagination(tableId) {
                    const tbody = document.getElementById(tableId).querySelector('tbody');
                    const rows = Array.from(tbody.querySelectorAll("tr.ticker-row"));
                    const totalItems = rows.length;
                    const totalPages = Math.ceil(totalItems / itemsPerPage) || 1;
                    
                    let currentPage = currentPages[tableId];
                    if (currentPage > totalPages) currentPage = totalPages;
                    if (currentPage < 1) currentPage = 1;
                    currentPages[tableId] = currentPage;

                    const start = (currentPage - 1) * itemsPerPage;
                    const end = start + itemsPerPage;

                    rows.forEach((row, index) => {
                        if (index >= start && index < end) {
                            row.style.display = "";
                        } else {
                            row.style.display = "none";
                        }
                    });

                    // Info text
                    const infoId = tableId === 'activeTable' ? 'activeInfo' : 'rejectedInfo';
                    const infoEl = document.getElementById(infoId);
                    const displayStart = totalItems === 0 ? 0 : start + 1;
                    const displayEnd = end > totalItems ? totalItems : end;
                    infoEl.textContent = \`Mostrando \${displayStart}-\${displayEnd} de \${totalItems}\`;

                    // Pagination buttons
                    const paginationId = tableId === 'activeTable' ? 'activePagination' : 'rejectedPagination';
                    const paginationEl = document.getElementById(paginationId);
                    paginationEl.innerHTML = '';

                    if (totalItems > 0) {
                        // Prev button
                        const prevLi = document.createElement('li');
                        prevLi.className = \`page-item \${currentPage === 1 ? 'disabled' : ''}\`;
                        prevLi.innerHTML = \`<a class="page-link" href="#" onclick="changePage('\${tableId}', \${currentPage - 1}); return false;">Anterior</a>\`;
                        paginationEl.appendChild(prevLi);

                        // Page numbers
                        for (let i = 1; i <= totalPages; i++) {
                            const li = document.createElement('li');
                            li.className = \`page-item \${currentPage === i ? 'active' : ''}\`;
                            li.innerHTML = \`<a class="page-link" href="#" onclick="changePage('\${tableId}', \${i}); return false;">\${i}</a>\`;
                            paginationEl.appendChild(li);
                        }

                        // Next button
                        const nextLi = document.createElement('li');
                        nextLi.className = \`page-item \${currentPage === totalPages ? 'disabled' : ''}\`;
                        nextLi.innerHTML = \`<a class="page-link" href="#" onclick="changePage('\${tableId}', \${currentPage + 1}); return false;">Siguiente</a>\`;
                        paginationEl.appendChild(nextLi);
                    }
                }

                function changePage(tableId, newPage) {
                    currentPages[tableId] = newPage;
                    updatePagination(tableId);
                }

                // Sorting
                let sortDirections = {};
                
                function sortTable(tableId, columnIndex, isNumeric = false) {
                    const table = document.getElementById(tableId);
                    const tbody = table.querySelector("tbody");
                    const rows = Array.from(tbody.querySelectorAll("tr.ticker-row"));
                    
                    if (rows.length === 0) return;

                    const sortKey = \`\${tableId}-\${columnIndex}\`;
                    if (sortDirections[sortKey] === undefined) {
                        sortDirections[sortKey] = true;
                    }
                    const ascending = sortDirections[sortKey];
                    
                    rows.sort((a, b) => {
                        let valA = a.cells[columnIndex].innerText.trim();
                        let valB = b.cells[columnIndex].innerText.trim();
                        
                        if (a.cells[columnIndex].hasAttribute('data-sort-value')) {
                            valA = a.cells[columnIndex].getAttribute('data-sort-value');
                            valB = b.cells[columnIndex].getAttribute('data-sort-value');
                        }

                        if (isNumeric) {
                            let numA = parseFloat(valA.split('/')[0]);
                            let numB = parseFloat(valB.split('/')[0]);
                            
                            numA = isNaN(numA) ? -1 : numA;
                            numB = isNaN(numB) ? -1 : numB;
                            
                            return ascending ? numA - numB : numB - numA;
                        } else {
                            return ascending ? valA.localeCompare(valB) : valB.localeCompare(valA);
                        }
                    });
                    
                    sortDirections[sortKey] = !ascending;
                    
                    rows.forEach(row => tbody.appendChild(row));
                    
                    currentPages[tableId] = 1;
                    updatePagination(tableId);
                }

                document.addEventListener('DOMContentLoaded', () => {
                    updatePagination('activeTable');
                    updatePagination('rejectedTable');
                });
