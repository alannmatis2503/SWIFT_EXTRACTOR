import React, {useState} from "react";
import axios from "axios";

export default function Upload({token, onLogout}){
  const [files,setFiles]=useState(null);
  const [resultUrl,setResultUrl]=useState(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState(null);

  const upload = async () => {
    if(!files || files.length===0) return alert("Choose files");
    const fd = new FormData();
    for(let i=0;i<files.length;i++) fd.append("files", files[i]);
    setLoading(true);
    setError(null);
    try {
      const r = await axios.post("/upload", fd, {
        headers: { Authorization: `Bearer ${token}`, "Content-Type":"multipart/form-data" },
        responseType: "blob"
      });
      const blob = new Blob([r.data], { type: r.headers['content-type'] });
      const url = window.URL.createObjectURL(blob);
      setResultUrl(url);
    } catch(e){
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{maxWidth:800}}>
      <div>
        <input type="file" multiple accept="application/pdf" onChange={e=>setFiles(e.target.files)} />
      </div>
      <div style={{marginTop:10}}>
        <button onClick={upload} disabled={loading}>{loading ? "Processing..." : "Upload & Extract"}</button>
        <button onClick={onLogout} style={{marginLeft:8}}>Logout</button>
      </div>
      {error && <div style={{color:"red", marginTop:8}}>{error}</div>}
      {resultUrl && <div style={{marginTop:12}}><a href={resultUrl} download>Download workbook</a></div>}
    </div>
  );
}
