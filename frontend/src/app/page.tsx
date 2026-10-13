"use client";

import { useState } from "react";
import { AudioLines, HardDrive, Play } from "lucide-react";
import VoiceCapture, { Model } from "@/components/VoiceCapture";
import SongConverter from "@/components/SongConverter";

export default function Home() {
  const [model, setModel] = useState<Model>({status: "missing", approved: false});
  const [tab, setTab] = useState("samples");
  const [capturing, setCapturing] = useState(false);
  function openPreview() {
    setTab("songs");
    requestAnimationFrame(()=>document.getElementById("voice-audition")?.scrollIntoView({behavior:"smooth",block:"start"}));
  }
  return <main>
    <header className="app-header"><div className="brand"><AudioLines size={28} /><h1>Your Voice, Your Song</h1></div><span className="local-tag"><HardDrive size={14} />Estudio local</span></header>
    <nav className="studio-tabs" aria-label="Vistas del estudio"><button disabled={capturing} aria-current={tab==="samples" ? "page" : undefined} onClick={()=>setTab("samples")}>Muestras vocales</button><button disabled={capturing} aria-current={tab==="songs" ? "page" : undefined} onClick={()=>setTab("songs")}>Canciones y prueba</button>{model.status==="trained" && !model.approved && !model.stale ? <div className="preview-shortcut"><span className="muted">Prueba pendiente</span><button className="secondary" disabled={capturing} onClick={openPreview}><Play size={15}/>Probar mi voz</button></div> : <span className="muted">{model.stale ? "Modelo pendiente de actualizar" : model.approved ? "Voz aprobada" : "Modelo personal pendiente"}</span>}</nav>
    <div hidden={tab!=="samples"}><VoiceCapture onModel={setModel} onPreview={openPreview} onCaptureState={setCapturing}/></div>
    <div hidden={tab!=="songs"}><SongConverter model={model} onModel={setModel}/></div>
    <footer className="app-footer"><span>Luis Felipe · Perfil bilingüe nuevo</span><span>RVC personal · Español / English</span></footer>
  </main>;
}
