const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
global.DOMParser = dom.window.DOMParser;
global.document = dom.window.document;

const BpmnViewer = require('bpmn-js/lib/Viewer').default;

let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
    try {
        const container = dom.window.document.createElement('div');
        const viewer = new BpmnViewer({ container });
        const result = await viewer.importXML(input);
        process.stdout.write(JSON.stringify({
            success: true,
            warnings: (result.warnings || []).map(w => w.message || String(w)),
        }));
    } catch (err) {
        process.stdout.write(JSON.stringify({
            success: false,
            error: err.message,
            warnings: [],
        }));
    }
});
