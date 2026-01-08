import React, {useState} from "react";
import axios from "axios";

export default function Upload({token, onLogout}){
  const [files,setFiles]=useState(null);
  const [resultUrl,setResultUrl]=useState(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState(null);
  const [direction, setDirection]=useState("incoming"); // "incoming" or "outgoing"
  const [dateStart, setDateStart]=useState(""); // Date de début
  const [dateEnd, setDateEnd]=useState(""); // Date de fin

  const upload = async () => {
    if(!files || files.length===0) return alert("Choose files");
    const fd = new FormData();
    for(let i=0;i<files.length;i++) fd.append("files", files[i]);
    fd.append("direction", direction); // Add direction to form data
    if(dateStart) fd.append("date_start", dateStart);
    if(dateEnd) fd.append("date_end", dateEnd);
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
      {/* Direction selector */}
      <div style={{marginBottom:20, borderBottom:"2px solid #ccc", paddingBottom:10}}>
        <div style={{display:"flex", gap:10}}>
          <button 
            onClick={()=>setDirection("incoming")}
            style={{
              padding:"10px 20px",
              backgroundColor: direction==="incoming" ? "#007bff" : "#e0e0e0",
              color: direction==="incoming" ? "white" : "black",
              border:"none",
              borderRadius:5,
              cursor:"pointer",
              fontWeight: direction==="incoming" ? "bold" : "normal"
            }}
          >
            Messages Entrants
          </button>
          <button 
            onClick={()=>setDirection("outgoing")}
            style={{
              padding:"10px 20px",
              backgroundColor: direction==="outgoing" ? "#007bff" : "#e0e0e0",
              color: direction==="outgoing" ? "white" : "black",
              border:"none",
              borderRadius:5,
              cursor:"pointer",
              fontWeight: direction==="outgoing" ? "bold" : "normal"
            }}
          >
            Messages Sortants
          </button>
        </div>
        <div style={{marginTop:8, fontSize:14, color:"#666"}}>
          {direction === "incoming" ? 
            "Extraction pour messages entrants (MT202, MT103, MT910)" : 
            "Extraction pour messages sortants (MT202, MT103, MT910)"}
        </div>
      </div>

      {/* File upload section */}
      <div>
        <input type="file" multiple accept="application/pdf" onChange={e=>setFiles(e.target.files)} />
      </div>
      
      {/* Date range filter */}
      <div style={{marginTop:15, padding:15, backgroundColor:"#f5f5f5", borderRadius:5}}>
        <div style={{fontWeight:"bold", marginBottom:10}}>Filtrer par plage de dates (optionnel):</div>
        <div style={{display:"flex", gap:15, alignItems:"center"}}>
          <div>
            <label style={{display:"block", marginBottom:5, fontSize:14}}>Date de début:</label>
            <input 
              type="date" 
              value={dateStart}
              onChange={e=>setDateStart(e.target.value)}
              style={{padding:8, borderRadius:4, border:"1px solid #ccc"}}
            />
          </div>
          <div>
            <label style={{display:"block", marginBottom:5, fontSize:14}}>Date de fin:</label>
            <input 
              type="date" 
              value={dateEnd}
              onChange={e=>setDateEnd(e.target.value)}
              style={{padding:8, borderRadius:4, border:"1px solid #ccc"}}
            />
          </div>
        </div>
        <div style={{marginTop:8, fontSize:12, color:"#666", fontStyle:"italic"}}>
          Laissez vide pour extraire tous les messages. Vous pouvez spécifier une seule date (début ou fin) ou les deux.
        </div>
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
