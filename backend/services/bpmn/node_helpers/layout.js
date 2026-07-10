const ELK = require('elkjs');
const { JSDOM } = require('jsdom');

const elk = new ELK();

const BPMN_NS = 'http://www.omg.org/spec/BPMN/20100524/MODEL';

const SIZES = {
    startEvent: { w: 36, h: 36 },
    endEvent: { w: 36, h: 36 },
    boundaryEvent: { w: 36, h: 36 },
    userTask: { w: 160, h: 80 },
    serviceTask: { w: 160, h: 80 },
    businessRuleTask: { w: 160, h: 80 },
    manualTask: { w: 160, h: 80 },
    task: { w: 160, h: 80 },
    exclusiveGateway: { w: 50, h: 50 },
    parallelGateway: { w: 50, h: 50 },
    inclusiveGateway: { w: 50, h: 50 },
};

async function layoutBpmn(xmlString) {
    const dom = new JSDOM(xmlString, { contentType: 'application/xml' });
    const doc = dom.window.document;

    // Extract collaboration ID
    const collab = doc.getElementsByTagNameNS(BPMN_NS, 'collaboration')[0];
    const collabId = collab ? collab.getAttribute('id') : 'Collaboration_1';

    // Extract participant
    const participant = doc.getElementsByTagNameNS(BPMN_NS, 'participant')[0];
    const participantId = participant ? participant.getAttribute('id') : null;

    // Extract all flow nodes
    const flowNodes = [];
    for (const [typeName, size] of Object.entries(SIZES)) {
        const els = doc.getElementsByTagNameNS(BPMN_NS, typeName);
        for (let i = 0; i < els.length; i++) {
            flowNodes.push({
                id: els[i].getAttribute('id'),
                type: typeName,
                width: size.w,
                height: size.h,
            });
        }
    }

    // Extract sequence flows
    const sequenceFlows = [];
    const sfEls = doc.getElementsByTagNameNS(BPMN_NS, 'sequenceFlow');
    for (let i = 0; i < sfEls.length; i++) {
        sequenceFlows.push({
            id: sfEls[i].getAttribute('id'),
            source: sfEls[i].getAttribute('sourceRef'),
            target: sfEls[i].getAttribute('targetRef'),
        });
    }

    // Extract lanes
    const lanes = [];
    const laneEls = doc.getElementsByTagNameNS(BPMN_NS, 'lane');
    for (let i = 0; i < laneEls.length; i++) {
        const lane = laneEls[i];
        const laneId = lane.getAttribute('id');
        const laneName = lane.getAttribute('name') || laneId;
        const refs = [];
        const refEls = lane.getElementsByTagNameNS(BPMN_NS, 'flowNodeRef');
        for (let j = 0; j < refEls.length; j++) {
            refs.push(refEls[j].textContent.trim());
        }
        lanes.push({ id: laneId, name: laneName, refs });
    }

    // Build ELK graph. To get nodes placed into the right swim lane
    // (horizontal band), we lay out each lane as a separate ELK pass
    // and stack them vertically. ELK's hierarchical / partition modes
    // don't reliably keep nodes confined to row bands when direction
    // is RIGHT — partitioning creates columns, not rows. The flat
    // approach with post-hoc lane-bounds computation collapsed every
    // lane to the top because all rows shared similar Y values.
    const nodeIdSet = new Set(flowNodes.map(n => n.id));

    // Lane index lookup: node_id -> lane index. Default 0 for orphans.
    const nodeLane = {};
    lanes.forEach((lane, idx) => {
        for (const ref of lane.refs) nodeLane[ref] = idx;
    });

    const useLanes = lanes.length > 0;

    let posMap = {};
    let edgeMap = {};

    if (useLanes) {
        // Pass 1 — lay out the FULL graph flat to get the X order right.
        // We then keep the X coordinates per node, and rewrite Y based on
        // which lane each node belongs to. This gives ELK its full picture
        // for layering / edge routing while still respecting lane rows.
        const flatGraph = {
            id: 'root',
            layoutOptions: {
                'elk.algorithm': 'layered',
                'elk.direction': 'RIGHT',
                'elk.spacing.nodeNode': '40',
                'elk.layered.spacing.nodeNodeBetweenLayers': '80',
                'elk.edgeRouting': 'ORTHOGONAL',
                'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
                'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
            },
            children: flowNodes.map(n => ({
                id: n.id, width: n.width, height: n.height,
            })),
            edges: sequenceFlows
                .filter(sf => nodeIdSet.has(sf.source) && nodeIdSet.has(sf.target))
                .map(sf => ({ id: sf.id, sources: [sf.source], targets: [sf.target] })),
        };
        const flatResult = await elk.layout(flatGraph);

        const flatPos = {};
        for (const child of flatResult.children || []) {
            flatPos[child.id] = { x: child.x, w: child.width, h: child.height };
        }

        // Compute per-lane row Y bands. Each lane gets enough vertical
        // room for the tallest node assigned to it, plus padding.
        const LANE_PADDING = 20;
        const LANE_MIN_HEIGHT = 80;
        const laneHeights = lanes.map((lane) => {
            let maxH = LANE_MIN_HEIGHT;
            for (const ref of lane.refs) {
                const p = flatPos[ref];
                if (p && p.h > maxH) maxH = p.h;
            }
            return maxH + LANE_PADDING * 2;
        });
        const laneTops = [];
        let runningY = 0;
        for (const h of laneHeights) {
            laneTops.push(runningY);
            runningY += h;
        }

        // Final positions: keep ELK's X, override Y to center the node
        // inside its assigned lane band.
        for (const n of flowNodes) {
            const fp = flatPos[n.id];
            if (!fp) continue;
            const laneIdx = nodeLane[n.id] != null ? nodeLane[n.id] : 0;
            const band = laneTops[laneIdx];
            const bandHeight = laneHeights[laneIdx];
            const centeredY = band + (bandHeight - fp.h) / 2;
            posMap[n.id] = { x: fp.x, y: centeredY, w: fp.w, h: fp.h };
        }

        // Manhattan-route every edge from the lane-aware positions. ELK's
        // second-pass interactive layout produced bend points that didn't
        // match the post-override Y values, leaving arrows shooting into
        // empty space. Direct orthogonal routing on the final coords is
        // deterministic and lane-correct.
        for (const sf of sequenceFlows) {
            if (!nodeIdSet.has(sf.source) || !nodeIdSet.has(sf.target)) continue;
            const sp = posMap[sf.source];
            const tp = posMap[sf.target];
            if (!sp || !tp) continue;
            const sx = sp.x + sp.w;
            const sy = sp.y + sp.h / 2;
            const ex = tp.x;
            const ey = tp.y + tp.h / 2;
            if (Math.abs(sy - ey) < 1) {
                edgeMap[sf.id] = [{ x: sx, y: sy }, { x: ex, y: ey }];
            } else if (ex > sx + 20) {
                const mx = (sx + ex) / 2;
                edgeMap[sf.id] = [
                    { x: sx, y: sy },
                    { x: mx, y: sy },
                    { x: mx, y: ey },
                    { x: ex, y: ey },
                ];
            } else {
                // target sits left of source (loop-back) — route around
                const detourY = Math.min(sp.y, tp.y) - 30;
                edgeMap[sf.id] = [
                    { x: sx, y: sy },
                    { x: sx + 20, y: sy },
                    { x: sx + 20, y: detourY },
                    { x: ex - 20, y: detourY },
                    { x: ex - 20, y: ey },
                    { x: ex, y: ey },
                ];
            }
        }
    } else {
        // No lanes — original flat layout.
        const elkGraph = {
            id: 'root',
            layoutOptions: {
                'elk.algorithm': 'layered',
                'elk.direction': 'RIGHT',
                'elk.spacing.nodeNode': '40',
                'elk.layered.spacing.nodeNodeBetweenLayers': '80',
                'elk.edgeRouting': 'ORTHOGONAL',
                'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
                'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
            },
            children: flowNodes.map(n => ({ id: n.id, width: n.width, height: n.height })),
            edges: sequenceFlows
                .filter(sf => nodeIdSet.has(sf.source) && nodeIdSet.has(sf.target))
                .map(sf => ({ id: sf.id, sources: [sf.source], targets: [sf.target] })),
        };
        const result = await elk.layout(elkGraph);
        for (const child of result.children || []) {
            posMap[child.id] = { x: child.x, y: child.y, w: child.width, h: child.height };
        }
        for (const edge of result.edges || []) {
            const waypoints = [];
            if (edge.sections) {
                for (const section of edge.sections) {
                    waypoints.push(section.startPoint);
                    if (section.bendPoints) waypoints.push(...section.bendPoints);
                    waypoints.push(section.endPoint);
                }
            }
            edgeMap[edge.id] = waypoints;
        }
    }

    // Compute pool bounds from all node positions
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const pos of Object.values(posMap)) {
        minX = Math.min(minX, pos.x);
        minY = Math.min(minY, pos.y);
        maxX = Math.max(maxX, pos.x + pos.w);
        maxY = Math.max(maxY, pos.y + pos.h);
    }
    // Also account for edge waypoints that may extend beyond nodes
    for (const wps of Object.values(edgeMap)) {
        for (const wp of wps) {
            minX = Math.min(minX, wp.x);
            minY = Math.min(minY, wp.y);
            maxX = Math.max(maxX, wp.x);
            maxY = Math.max(maxY, wp.y);
        }
    }

    const poolPad = 50;
    const poolHeader = 30;
    const poolX = Math.round(minX - poolPad - poolHeader);
    const poolY = Math.round(minY - poolPad);
    const poolW = Math.round((maxX - minX) + poolPad * 2 + poolHeader);
    const poolH = Math.round((maxY - minY) + poolPad * 2);

    // ── Build BPMNDiagram XML ──
    const di = [];
    di.push('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">');
    di.push(`    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="${collabId}">`);

    // Participant (pool) shape
    if (participantId) {
        di.push(`      <bpmndi:BPMNShape id="${participantId}_di" bpmnElement="${participantId}" isHorizontal="true">`);
        di.push(`        <dc:Bounds x="${poolX}" y="${poolY}" width="${poolW}" height="${poolH}" />`);
        di.push('      </bpmndi:BPMNShape>');
    }

    // Lane shapes
    if (lanes.length > 0) {
        const laneBounds = {};
        for (const lane of lanes) {
            let lMinY = Infinity, lMaxY = -Infinity;
            for (const ref of lane.refs) {
                const pos = posMap[ref];
                if (pos) {
                    lMinY = Math.min(lMinY, pos.y);
                    lMaxY = Math.max(lMaxY, pos.y + pos.h);
                }
            }
            laneBounds[lane.id] = { minY: lMinY, maxY: lMaxY };
        }

        const sorted = [...lanes].sort((a, b) =>
            (laneBounds[a.id].minY === Infinity ? 0 : laneBounds[a.id].minY) -
            (laneBounds[b.id].minY === Infinity ? 0 : laneBounds[b.id].minY)
        );

        // Distribute pool height among lanes proportionally
        let laneY = poolY;
        for (const lane of sorted) {
            const bounds = laneBounds[lane.id];
            const laneH = bounds.minY === Infinity
                ? 60
                : Math.round((bounds.maxY - bounds.minY) + poolPad);

            di.push(`      <bpmndi:BPMNShape id="${lane.id}_di" bpmnElement="${lane.id}" isHorizontal="true">`);
            di.push(`        <dc:Bounds x="${poolX + poolHeader}" y="${Math.round(laneY)}" width="${poolW - poolHeader}" height="${laneH}" />`);
            di.push('      </bpmndi:BPMNShape>');

            laneY += laneH;
        }
    }

    // Flow node shapes
    for (const node of flowNodes) {
        const pos = posMap[node.id];
        if (!pos) continue;
        const isEvent = node.type.includes('Event');
        const isGateway = node.type.includes('Gateway');

        di.push(`      <bpmndi:BPMNShape id="${node.id}_di" bpmnElement="${node.id}">`);
        di.push(`        <dc:Bounds x="${Math.round(pos.x)}" y="${Math.round(pos.y)}" width="${Math.round(pos.w)}" height="${Math.round(pos.h)}" />`);
        if (isEvent || isGateway) {
            di.push('        <bpmndi:BPMNLabel>');
            di.push(`          <dc:Bounds x="${Math.round(pos.x - 10)}" y="${Math.round(pos.y + pos.h + 5)}" width="${Math.round(pos.w + 20)}" height="14" />`);
            di.push('        </bpmndi:BPMNLabel>');
        }
        di.push('      </bpmndi:BPMNShape>');
    }

    // Edge shapes (sequence flow arrows)
    for (const sf of sequenceFlows) {
        const waypoints = edgeMap[sf.id];
        if (!waypoints || waypoints.length < 2) {
            // Fallback: straight line from source right-center to target left-center
            const srcPos = posMap[sf.source];
            const tgtPos = posMap[sf.target];
            if (srcPos && tgtPos) {
                di.push(`      <bpmndi:BPMNEdge id="${sf.id}_di" bpmnElement="${sf.id}">`);
                di.push(`        <di:waypoint x="${Math.round(srcPos.x + srcPos.w)}" y="${Math.round(srcPos.y + srcPos.h / 2)}" />`);
                di.push(`        <di:waypoint x="${Math.round(tgtPos.x)}" y="${Math.round(tgtPos.y + tgtPos.h / 2)}" />`);
                di.push('      </bpmndi:BPMNEdge>');
            }
            continue;
        }

        di.push(`      <bpmndi:BPMNEdge id="${sf.id}_di" bpmnElement="${sf.id}">`);
        for (const wp of waypoints) {
            di.push(`        <di:waypoint x="${Math.round(wp.x)}" y="${Math.round(wp.y)}" />`);
        }
        di.push('      </bpmndi:BPMNEdge>');
    }

    di.push('    </bpmndi:BPMNPlane>');
    di.push('  </bpmndi:BPMNDiagram>');

    // Inject namespace declarations and DI into original XML
    let output = xmlString;

    if (!output.includes('xmlns:dc=')) {
        output = output.replace(
            'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"',
            'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"\n                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"\n                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"'
        );
    } else if (!output.includes('xmlns:di=')) {
        output = output.replace(
            'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"',
            'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"\n                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"'
        );
    }

    output = output.replace('</bpmn:definitions>', di.join('\n') + '\n</bpmn:definitions>');
    return output;
}

// CLI: read BPMN XML from stdin, write laid-out XML to stdout
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
    try {
        const result = await layoutBpmn(input);
        process.stdout.write(result);
    } catch (err) {
        process.stderr.write(err.message || String(err));
        process.exit(1);
    }
});
