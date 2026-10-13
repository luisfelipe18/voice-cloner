"use client";

import { useRef, useState } from "react";
import { Download, Upload, Loader2 } from "lucide-react";
import { request } from "@/lib/api";
import type { Model } from "./VoiceCapture";

export default function ModelTransfer({model, ready, disabled, onModel, onBusy}: {
  model?: Model; ready: boolean; disabled: boolean; onModel: (model: Model)=>void; onBusy: (busy: boolean)=>void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [trusted, setTrusted] = useState(false);
  const pending = useRef(false);
  function start(action: string) {
    if(pending.current) return false;
    pending.current=true;setBusy(action);setError("");onBusy(true);return true;
  }
  function finish() {pending.current=false;setBusy("");onBusy(false);}
  async function exportBundle(kind: "model" | "training") {
    if(!start(kind)) return;
    try {
      const result=await request<{download_url: string; filename: string}>("/studio/luis_felipe/bundles/export",{
        method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind}),
      });
      const link=document.createElement("a");link.href=result.download_url;link.download=result.filename;
      document.body.appendChild(link);link.click();link.remove();
    } catch(err) {setError(err instanceof Error ? err.message : "No se pudo exportar.");}
    finally {finish();}
  }
  async function importBundle(file: File) {
    if(!trusted || !start("import")) return;
    try {
      if(!file.name.toLowerCase().endsWith(".zip") || file.size>512*1024*1024 || !file.size) throw new Error("Selecciona un ZIP de modelo, no un paquete de entrenamiento (máximo 512 MB).");
      const form=new FormData();form.append("bundle",file);
      onModel(await request<Model>("/studio/luis_felipe/bundles/import",{method:"POST",body:form}));
      setTrusted(false);
    } catch(err) {setError(err instanceof Error ? err.message : "No se pudo importar el modelo.");}
    finally {finish();}
  }
  const locked=disabled || !!busy || model?.status==="training" || model?.preview?.status==="processing";
  return <div className="model-transfer"><div className="review-heading"><h3>Modelo portátil</h3>{busy && <span role="status"><Loader2 size={15} className="spin"/>Preparando paquete...</span>}</div>
    <div className="transfer-actions"><button className="secondary" disabled={locked || model?.status!=="trained"} onClick={()=>exportBundle("model")}><Download size={16}/>Exportar modelo</button>
      <button className="secondary" disabled={locked || !ready || model?.inference_only} onClick={()=>exportBundle("training")}><Download size={16}/>Paquete privado para Colab</button>
      <label className={`secondary upload-button ${locked || !trusted ? "disabled" : ""}`}><Upload size={16}/>Importar modelo<input type="file" accept=".zip" aria-label="Importar modelo exportado" disabled={locked || !trusted} onChange={event=>{const file=event.target.files?.[0];event.target.value="";if(file)importBundle(file);}}/></label>
    </div><label className="check-label"><input type="checkbox" checked={trusted} disabled={locked} onChange={event=>setTrusted(event.target.checked)}/>El modelo procede de mi propio entrenamiento o de una fuente de confianza.</label>
    <p className="transfer-privacy">Modelo: sin grabaciones. Paquete privado: contiene tu voz y checkpoints de entrenamiento.</p>
    {error && <p className="error-text" role="alert">{error}</p>}
  </div>;
}
