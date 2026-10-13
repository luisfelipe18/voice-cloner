"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Loader2, Music2, Play, RefreshCw, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { request, Song } from "@/lib/api";
import AudioCompare from "./AudioCompare";
import { Model } from "./VoiceCapture";

type TrashedSong = { song_id: string; name: string; status: string };

export default function SongConverter({ model, onModel }: { model: Model; onModel: (model: Model) => void }) {
  const [songs, setSongs] = useState<Song[]>([]);
  const [trash, setTrash] = useState<TrashedSong[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [libraryError, setLibraryError] = useState("");
  const [uploadNotice, setUploadNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [pitch, setPitch] = useState(0);
  const [uploadLanguage, setUploadLanguage] = useState("es");
  const [previewStart, setPreviewStart] = useState(30);
  const [listened, setListened] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const libraryRevision = useRef(0);
  useEffect(()=>setListened(false),[model.preview?.url]);
  useEffect(()=>{if(model.preview?.pitch_shift !== undefined) setPitch(model.preview.pitch_shift);},[model.preview?.pitch_shift]);
  useEffect(() => {
    let active = true;
    let latestPoll = 0;
    const poll = async () => {
      const revision = libraryRevision.current, pollId = ++latestPoll;
      const current = () => active && revision===libraryRevision.current && pollId===latestPoll;
      try {
        const data = await request<{ songs: Song[]; uploads: Song[]; trash?: TrashedSong[] }>("/songs/library");
        if (current()) { setSongs([...data.songs, ...data.uploads]); setTrash(data.trash || []); setLibraryError(""); }
      } catch (err) { if(current()) setLibraryError(err instanceof Error ? err.message : "Servidor no disponible."); }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => { active=false; clearInterval(interval); };
  }, [refresh]);
  const activeSong = songs.find(song => song.song_id === selected) || songs[0];
  const completed = songs.filter(song => song.status === "completed").length;
  const needsConversion = songs.some(song=>song.status!=="completed" || (song.proof?.conversion.pitch_shift ?? 0)!==pitch);
  const processing = songs.some(song => ["queued", "processing"].includes(song.status));
  const ready = model.approved && !model.stale;
  const options = {user_id: "luis_felipe", pitch_shift: pitch};

  async function convert(id?: string) {
    setBusy(true); setError("");
    try {
      await request(id ? `/songs/${id}/convert` : "/songs/library/convert", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...options, language:songs.find(song=>song.song_id===id)?.language})});
      setRefresh(value => value + 1);
    } catch (err) { setError(err instanceof Error ? err.message : "Error de conversion."); }
    finally { setBusy(false); }
  }
  async function upload(file?: File) {
    if (!file) return false;
    if(!file.size || file.size>100*1024*1024) {
      setError(file.size ? "La canción supera el límite de 100 MB." : "El archivo está vacío.");
      return false;
    }
    libraryRevision.current += 1; setUploadNotice("");
    setUploading(true); setError("");
    try {
      const form = new FormData(); form.append("song", file);
      form.append("language",uploadLanguage);
      Object.entries(options).forEach(([key,value]) => form.append(key,String(value)));
      const song = await request<Song>("/songs", {method:"POST",body:form});
      libraryRevision.current += 1;
      setSongs(current=>[...current.filter(item=>item.song_id!==song.song_id),song]);
      setSelected(song.song_id); setUploadNotice(`Canción añadida: ${song.name}`); setRefresh(value => value + 1);
      return true;
    } catch (err) { setError(err instanceof Error ? err.message : "Error subiendo audio."); return false; }
  }

  async function language(song: Song, value: string) {
    setBusy(true); setError("");
    try {await request(`/songs/${song.song_id}/language`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({language:value})});setSongs(current=>current.map(item=>item.song_id===song.song_id ? {...item,language:value} : item));setRefresh(value=>value+1);}
    catch(err) {setError(err instanceof Error ? err.message : "No se pudo guardar el idioma.");}
    finally {setBusy(false);}
  }
  async function uploadMany(files: File[]) {
    if(!files.length) return;
    setUploading(true);
    try {for (const file of files) {if(!await upload(file)) break;}}
    finally {setUploading(false);}
  }
  async function removeSong(song: Song) {
    if (!window.confirm(`¿Borrar "${song.name}" de la biblioteca? Se moverá a Papelera. Podrás restaurar la canción; los audios originales y procesados se conservarán.`)) return;
    setBusy(true); setError("");
    try {
      const removed = await request<TrashedSong>(`/songs/${song.song_id}`, {method:"DELETE"});
      setSongs(current=>current.filter(item=>item.song_id!==song.song_id));
      setTrash(current=>[...current.filter(item=>item.song_id!==removed.song_id),removed]);
      libraryRevision.current += 1;
      setRefresh(value=>value+1);
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo borrar la canción."); }
    finally { setBusy(false); }
  }
  async function restoreSong(song: TrashedSong) {
    setBusy(true); setError("");
    try {
      await request(`/songs/${song.song_id}/restore`, {method:"POST"});
      setTrash(current=>current.filter(item=>item.song_id!==song.song_id));
      libraryRevision.current += 1;
      setRefresh(value=>value+1);
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo restaurar la canción."); }
    finally { setBusy(false); }
  }
  async function preview() {
    if(!activeSong) return;
    setBusy(true); setError(""); setListened(false);
    try {const data=await request<Model>("/studio/luis_felipe/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({song_id:activeSong.song_id,start_seconds:previewStart,language:activeSong.language,pitch_shift:pitch})});onModel(data);}
    catch(err) {setError(err instanceof Error ? err.message : "Error generando la prueba.");} finally {setBusy(false);}
  }
  async function approve() {
    setBusy(true); setError("");
    try {onModel(await request<Model>("/studio/luis_felipe/approve",{method:"POST"}));}
    catch(err) {setError(err instanceof Error ? err.message : "No se pudo aprobar.");} finally {setBusy(false);}
  }

  return <div className="song-workspace">
    {model.status==="trained" && <section id="voice-audition" className="voice-audition" aria-labelledby="audition-title"><div className="review-heading"><h2 id="audition-title">Prueba de mi voz <small>15 segundos</small></h2><span className="muted">{model.stale ? "Muestras modificadas; vuelve a entrenar" : model.approved ? "Voz aprobada" : "Modelo entrenado · prueba pendiente"}</span></div>
      <div className="preview-controls"><label className="preview-song">Canción<select aria-label="Canción para la prueba" disabled={busy || model.preview?.status==="processing"} value={activeSong?.song_id || ""} onChange={e=>setSelected(e.target.value)}><option value="" disabled>Selecciona una canción</option>{songs.map(song=><option key={song.song_id} value={song.song_id}>{song.name.replace(/\.\w+$/, "").replace(/-/g," ")}</option>)}</select></label><label>Idioma<select aria-label="Idioma de la prueba" disabled={!activeSong || busy || model.preview?.status==="processing"} value={activeSong?.language || ""} onChange={e=>{if(activeSong)language(activeSong,e.target.value);}}><option value="" disabled>Selecciona el idioma</option><option value="es">Español</option><option value="en">English</option></select></label><label>Tono<select aria-label="Tono de la prueba" disabled={busy || model.preview?.status==="processing"} value={pitch} onChange={e=>setPitch(Number(e.target.value))}><option value={0}>Original</option><option value={-12}>Una octava abajo</option><option value={12}>Una octava arriba</option></select></label><label>Inicio (s)<input aria-label="Inicio de la prueba en segundos" disabled={busy || model.preview?.status==="processing"} type="number" min={0} value={previewStart} onChange={e=>setPreviewStart(Math.max(0,Number(e.target.value)))}/></label><button className="primary" disabled={model.stale || !activeSong?.language || busy || processing || model.preview?.status==="processing"} onClick={preview}>{model.preview?.status==="processing" ? <Loader2 size={16} className="spin"/> : <Play size={16}/>} {model.preview?.status==="processing" ? "Generando..." : "Generar prueba"}</button></div>
      {processing && <p className="muted preview-status" role="status">Conversión de canciones en curso.</p>}
      {activeSong && !activeSong.language && <p className="capture-note" role="status">Falta el idioma de la canción seleccionada.</p>}
      {model.preview?.status==="processing" && <p className="muted preview-status" role="status">Separando la voz y aplicando tu modelo personal...</p>}
      {model.preview?.error && <p className="error-text" role="alert">{model.preview.error}</p>}
      {model.preview?.status==="completed" && <><p className="muted preview-status">{model.preview.song} · {model.preview.pitch_shift===-12 ? "Una octava abajo" : model.preview.pitch_shift===12 ? "Una octava arriba" : "Tono original"}</p><div className={`audition-pair ${model.preview.previous_url ? "audition-three" : ""}`}><label>Original<audio controls src={model.preview.original_url}/></label>{model.preview.previous_url && <label>Modelo anterior<audio controls src={model.preview.previous_url}/></label>}<label>{model.preview.previous_url ? "Voz actualizada" : "Mi voz personal"}<audio controls src={model.preview.url} onPlay={()=>setListened(true)}/></label></div><button className="primary" disabled={!listened || busy || model.approved || model.stale} onClick={approve}><Check size={16}/>{model.approved ? "Voz aprobada" : "El parecido me convence: aprobar voz"}</button></>}
    </section>}
    <div className="library-heading"><div><span className="eyebrow">Biblioteca</span><h2>Mis canciones <small>{songs.length}</small></h2></div>
      <div className="upload-song-controls"><select aria-label="Idioma de la cancion nueva" disabled={uploading || busy} value={uploadLanguage} onChange={e=>setUploadLanguage(e.target.value)}><option value="es">Español</option><option value="en">English</option></select><label className={`secondary upload-button ${uploading || busy ? "disabled" : ""}`}>{uploading ? <Loader2 size={16} className="spin"/> : <Upload size={16}/>} {uploading ? "Subiendo..." : "Agregar cancion"}<input aria-label="Agregar cancion" type="file" multiple accept=".mp3,.wav,.flac,.m4a,.ogg,.webm" disabled={uploading || busy} onChange={e => {uploadMany(Array.from(e.target.files || [])); e.target.value="";}} /></label></div></div>
    {uploadNotice && <div className="upload-feedback" role="status"><Check size={17}/><span>{uploadNotice}</span><button className="icon-button" title="Cerrar confirmación" aria-label="Cerrar confirmación" onClick={()=>setUploadNotice("")}><X size={16}/></button></div>}
    <div className="conversion-settings"><label htmlFor="pitch">Tono <select id="pitch" disabled={busy || processing || model.preview?.status==="processing"} value={pitch} onChange={e=>setPitch(Number(e.target.value))}><option value={0}>Original</option><option value={-12}>Una octava abajo</option><option value={12}>Una octava arriba</option></select></label>
      <span className="muted">{ready ? "Modelo personal aprobado" : "Conversión completa bloqueada hasta aprobar tu voz"}</span>
      <button className="primary" disabled={!ready || busy || processing || songs.length === 0 || !needsConversion || songs.some(song=>!song.language)} onClick={()=>convert()}><Music2 size={17} />Convertir pendientes</button></div>
    {(error || libraryError) && <div role="alert" className="error-banner"><CircleAlert size={17} /><span>{error || libraryError}</span><button className="icon-button" aria-label="Actualizar biblioteca" title="Actualizar biblioteca" onClick={()=>{setError("");setRefresh(value=>value+1);}}><RefreshCw size={16} /></button></div>}
    <div className="song-list">{songs.map((song,index) => <div key={song.song_id} className={`song-row ${activeSong?.song_id===song.song_id ? "selected" : ""}`}>
      <button className="song-select" onClick={()=>setSelected(song.song_id)}><span className="track-number">{String(index+1).padStart(2,"0")}</span><Music2 size={18} /><span className="track-details"><strong>{song.name.replace(/\.\w+$/, "").replace(/-/g," ")}</strong><span>{song.status === "processing" ? song.stage : song.status === "failed" ? song.error : song.status === "queued" ? "En cola" : song.status === "completed" ? "Mi voz personal disponible" : song.status === "legacy" ? "Prueba anterior descartada" : "Original"}</span></span></button>
      <select className="song-language" aria-label={`Idioma de ${song.name}`} value={song.language || ""} disabled={busy || ["queued","processing"].includes(song.status)} onChange={e=>language(song,e.target.value)}><option value="" disabled>Idioma</option><option value="es">Español</option><option value="en">English</option></select>
      <div className={`track-status ${song.status}`}>{song.status === "completed" ? <><Check size={15} />Lista</> : song.status === "processing" ? <><Loader2 size={15} className="spin" />{song.progress}%</> : song.status === "queued" ? "En cola" : song.status === "failed" ? <CircleAlert size={17} /> : "Pendiente"}</div>
      <button className="icon-button" disabled={!ready || !song.language || busy || ["processing","queued"].includes(song.status)} title={song.status === "completed" ? "Volver a convertir" : "Convertir cancion"} aria-label={`${song.status === "completed" ? "Volver a convertir" : "Convertir"} ${song.name}`} onClick={()=>convert(song.song_id)}>{song.status === "completed" || song.status === "failed" ? <RefreshCw size={17} /> : <Play size={17} />}</button>
      <button className="icon-button" disabled={busy || ["processing","queued"].includes(song.status)} title="Borrar canción" aria-label={`Borrar canción ${song.name}`} onClick={()=>removeSong(song)}><Trash2 size={17}/></button>
      {song.status === "processing" && <div className="row-progress"><i style={{width:`${song.progress}%`}} /></div>}
    </div>)}</div>
    {trash.length > 0 && <details className="song-trash"><summary>Papelera · {trash.length}</summary><div className="song-trash-list">{trash.map(song=><div key={song.song_id}><span>{song.name}</span><button className="icon-button" disabled={busy} title="Restaurar canción" aria-label={`Restaurar canción ${song.name}`} onClick={()=>restoreSong(song)}><RotateCcw size={16}/></button></div>)}</div></details>}
    <div className="library-footer"><span>{completed} de {songs.length} convertidas</span><span>songs-voice-replaced</span></div>
    {activeSong && <AudioCompare song={activeSong} />}
  </div>;
}
