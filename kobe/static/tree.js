// tree.js

async function drawTree(container, postId, treeType) {
    const svgSelection = d3.select(container).select("svg");
    
    let rawData;
    try {
        const response = await fetch(`${API_BASE_URL}/api/tree/${encodeURIComponent(postId)}/${encodeURIComponent(treeType)}`);
        rawData = await response.json();

        if (!rawData || !rawData.name) {
            rawData = { name: "データなし", children: [] };
        }
    } catch (error) {
        rawData = { name: "データ取得エラー", children: [] };
    }

    svgSelection.remove();
    d3.select(container).selectAll("svg").remove();

    const width = 750, height = 180;
    
    const svgElement = d3.select(container)
        .append("svg")
        .attr("width", "100%")
        .attr("height", height)
        .style("opacity", 0);

    const zoom = d3.zoom()
        .scaleExtent([0.5, 3])
        .on("zoom", (event) => {
            svgGroup.attr("transform", event.transform);
        });

    svgElement.call(zoom);

    const svgGroup = svgElement.append("g")
        .attr("transform", "translate(40,20)");

    svgElement.transition()
        .duration(300)
        .style("opacity", 1);

    const root = d3.hierarchy(rawData);
    d3.tree().size([height - 40, width - 200])(root);

    svgGroup.selectAll(".link")
        .data(root.links())
        .enter().append("path")
        .attr("fill", "none")
        .attr("stroke", "#8ab4f8")
        .attr("stroke-width", 1.5)
        .attr("opacity", 0.7)
        .attr("d", d3.linkHorizontal().x(d => d.y).y(d => d.x));

    const node = svgGroup.selectAll(".node")
        .data(root.descendants())
        .enter().append("g")
        .attr("transform", d => `translate(${d.y},${d.x})`);

    node.append("circle")
        .attr("r", 0)
        .attr("fill", "#ffffff")
        .transition()
        .duration(300)
        .attr("r", 3.5);

    node.append("text")
        .attr("dy", 3)
        .attr("x", 8)
        .attr("font-size", "11px")
        .attr("fill", "#e3e3e3")
        .style("opacity", 0)
        .text(d => d.data.name)
        .transition()
        .duration(300)
        .style("opacity", 1);
}

function switchTreeTab(button, postId, treeType) {
    const container = button.closest('.tree-container');
    container.querySelectorAll('.tree-tab-btn').forEach(btn => btn.classList.remove('active'));
    button.classList.add('active');

    const canvas = container.querySelector('.tree-canvas');
    drawTree(canvas, postId, treeType);
}

function toggleLogicTree(headerElement, postId) {
    const parent = headerElement.parentElement;
    let treeContainer = parent.querySelector('.tree-container');
    
    if (treeContainer) {
        treeContainer.style.animation = 'fadeIn 0.2s ease-out reverse';
        setTimeout(() => treeContainer.remove(), 180);
        return;
    }

    parent.querySelectorAll('.tree-container').forEach(el => el.remove());

    treeContainer = document.createElement('div');
    treeContainer.className = 'tree-container';
    treeContainer.innerHTML = `
        <div class="tree-header">
            <div class="tree-tabs">
                <button class="tree-tab-btn active" onclick="switchTreeTab(this, '${postId}', 'What')">要素分解</button>
            </div>
        </div>
        <div class="tree-canvas"></div>
    `;
    parent.appendChild(treeContainer);

    const canvas = treeContainer.querySelector('.tree-canvas');
    drawTree(canvas, postId, 'What');
}