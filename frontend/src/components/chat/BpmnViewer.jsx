import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

export default function BpmnViewer({ bpmnId, inlineXml, height = 400 }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let viewer = null;
    let cancelled = false;

    const init = async () => {
      if (!bpmnId && !inlineXml) {
        setError('No diagram ID provided');
        setLoading(false);
        return;
      }
      try {
        let xml = inlineXml;
        if (!xml) {
          // 1. Fetch XML first (before creating viewer) so network latency
          //    gives the container time to paint with real dimensions
          const res = await api.get(`${API_URL}/api/bpmn/${bpmnId}`);
          if (cancelled) return;
          if (!res.ok) {
            setError('Failed to load diagram');
            setLoading(false);
            return;
          }
          const data = await res.json();
          xml = data.bpmn_xml || data.edited_xml;
        }
        if (!xml) {
          setError('No diagram XML found');
          setLoading(false);
          return;
        }

        // 2. Create viewer — container should have dimensions by now
        const { default: BpmnNavigatedViewer } = await import('bpmn-js/lib/NavigatedViewer');
        if (cancelled) return;

        viewer = new BpmnNavigatedViewer({ container: containerRef.current });
        viewerRef.current = viewer;

        // 3. Import XML
        const result = await viewer.importXML(xml);
        if (cancelled) return;
        if (result.warnings?.length) {
          console.warn('BPMN import warnings:', result.warnings);
        }

        // 4. Yield to browser paint cycle, then zoom to fit
        await new Promise(r => setTimeout(r, 0));
        if (cancelled || !viewerRef.current) return;

        const canvas = viewer.get('canvas');
        canvas.zoom('fit-viewport');
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Failed to render diagram');
          setLoading(false);
        }
      }
    };

    init();

    return () => {
      cancelled = true;
      if (viewer) {
        viewer.destroy();
        viewerRef.current = null;
      }
    };
  }, [bpmnId, inlineXml]);

  if (error) {
    return (
      <div className="p-4 rounded-lg border border-red-500/30 bg-red-500/5 text-red-600 dark:text-red-400 text-sm">
        {error}
      </div>
    );
  }

  return (
    <div className="relative rounded-lg border border-aegisaxiom-border bg-white overflow-hidden">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
          <div className="text-sm text-aegisaxiom-text-muted">Loading diagram...</div>
        </div>
      )}
      <div ref={containerRef} style={{ height: `${height}px`, width: '100%' }} />
    </div>
  );
}
