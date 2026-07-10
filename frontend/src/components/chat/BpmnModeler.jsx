import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import { Save, X, Loader2 } from 'lucide-react';

export default function BpmnModeler({ bpmnId, onClose }) {
  const containerRef = useRef(null);
  const modelerRef = useRef(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let modeler = null;
    const init = async () => {
      if (!bpmnId) {
        setError('No diagram ID provided');
        setLoading(false);
        return;
      }
      try {
        const { default: BpmnJsModeler } = await import('bpmn-js/lib/Modeler');
        modeler = new BpmnJsModeler({ container: containerRef.current });
        modelerRef.current = modeler;

        const res = await api.get(`${API_URL}/api/bpmn/${bpmnId}`);
        if (!res.ok) {
          setError('Failed to load diagram');
          setLoading(false);
          return;
        }
        const data = await res.json();
        await modeler.importXML(data.bpmn_xml);
        modeler.get('canvas').zoom('fit-viewport');
        setLoading(false);
      } catch (err) {
        setError(err.message || 'Failed to load modeler');
        setLoading(false);
      }
    };
    init();
    return () => {
      if (modeler) modeler.destroy();
    };
  }, [bpmnId]);

  const handleSave = async () => {
    if (!modelerRef.current) return;
    setSaving(true);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      const res = await api.patch(`${API_URL}/api/bpmn/${bpmnId}`, { edited_xml: xml });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || 'Save failed');
      } else {
        if (onClose) onClose();
      }
    } catch (err) {
      setError('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-aegisaxiom-border">
          <span className="text-sm font-semibold text-aegisaxiom-text">BPMN Diagram Editor</span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 disabled:opacity-40 text-white text-xs rounded-lg transition-colors"
            >
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              Save
            </button>
            <button
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Modeler canvas */}
        <div className="flex-1 relative bg-white">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
              <div className="text-sm text-aegisaxiom-text-muted">Loading editor...</div>
            </div>
          )}
          {error && (
            <div className="absolute top-3 left-3 right-3 z-20 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
              {error}
            </div>
          )}
          <div ref={containerRef} className="w-full h-full" />
        </div>
      </div>
    </div>
  );
}
