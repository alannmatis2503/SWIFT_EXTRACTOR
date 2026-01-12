import React, {useState} from "react";
import axios from "axios";

export default function Upload({token, onLogout}){
  const [files,setFiles]=useState(null);
  const [resultUrl,setResultUrl]=useState(null);
  const [resultFilename, setResultFilename]=useState(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState(null);
  const [direction, setDirection]=useState("incoming"); // "incoming", "outgoing" or "transfer_analysis"
  const [dateStart, setDateStart]=useState(""); // Date de début
  const [dateEnd, setDateEnd]=useState(""); // Date de fin
  const [extractionInfo, setExtractionInfo]=useState(null); // Info sur l'extraction

  const upload = async () => {
    if(!files || files.length===0) return alert("Choisissez des fichiers PDF");
    const fd = new FormData();
    for(let i=0;i<files.length;i++) fd.append("files", files[i]);
    fd.append("direction", direction); // Add direction to form data
    if(dateStart) fd.append("date_start", dateStart);
    if(dateEnd) fd.append("date_end", dateEnd);
    setLoading(true);
    setError(null);
    setExtractionInfo(null);
    
    // Choisir l'endpoint selon le mode
    const endpoint = direction === "transfer_analysis" ? "/upload_transfer_analysis" : "/upload";
    
    try {
      const r = await axios.post(endpoint, fd, {
        headers: { Authorization: `Bearer ${token}`, "Content-Type":"multipart/form-data" },
        responseType: "blob"
      });
      const blob = new Blob([r.data], { type: r.headers['content-type'] });
      const url = window.URL.createObjectURL(blob);
      // Récupérer le nom du fichier depuis le header Content-Disposition
      const contentDisposition = r.headers['content-disposition'];
      let filename = 'extraction.xlsx';
      if (contentDisposition) {
        const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (match && match[1]) {
          filename = match[1].replace(/['"]/g, '');
        }
      }
      setResultUrl(url);
      setResultFilename(filename);
      
      // Déterminer le label du type d'extraction
      let directionLabel;
      if (direction === "incoming") {
        directionLabel = "Messages Entrants";
      } else if (direction === "outgoing") {
        directionLabel = "Messages Sortants";
      } else {
        directionLabel = "Analyse Transferts Sortants Exécutés";
      }
      
      setExtractionInfo({
        direction: directionLabel,
        filesCount: files.length,
        timestamp: new Date().toLocaleString('fr-FR')
      });
    } catch(e){
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = () => {
    // Ne pas effacer l'URL après téléchargement - le fichier reste visible
    const link = document.createElement('a');
    link.href = resultUrl;
    link.download = resultFilename || 'extraction.xlsx';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleNewExtraction = () => {
    // Effacer les résultats pour une nouvelle extraction
    setResultUrl(null);
    setResultFilename(null);
    setExtractionInfo(null);
    setFiles(null);
    // Réinitialiser l'input file
    const fileInput = document.querySelector('input[type="file"]');
    if (fileInput) fileInput.value = '';
  };

  return (
    <div style={{maxWidth:800}}>
      {/* Direction selector */}
      <div style={{marginBottom:20, borderBottom:"2px solid #ccc", paddingBottom:10}}>
        <div style={{display:"flex", gap:10, flexWrap:"wrap"}}>
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
          <button 
            onClick={()=>setDirection("transfer_analysis")}
            style={{
              padding:"10px 20px",
              backgroundColor: direction==="transfer_analysis" ? "#ff9800" : "#e0e0e0",
              color: direction==="transfer_analysis" ? "white" : "black",
              border:"none",
              borderRadius:5,
              cursor:"pointer",
              fontWeight: direction==="transfer_analysis" ? "bold" : "normal"
            }}
          >
            Analyse Transferts Exécutés
          </button>
        </div>
        <div style={{marginTop:8, fontSize:14, color:"#666"}}>
          {direction === "incoming" ? 
            "Extraction pour messages entrants (MT202, MT103, MT910)" : 
            direction === "outgoing" ?
            "Extraction pour messages sortants (MT202, MT103, MT910)" :
            "Analyse des transferts sortants exécutés (MT202/MT103 + fin.900)"}
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
      {resultUrl && (
        <div style={{marginTop:15, padding:15, backgroundColor:"#e8f5e9", borderRadius:5, border:"1px solid #4caf50"}}>
          <div style={{fontWeight:"bold", color:"#2e7d32", marginBottom:10}}>
            ✅ Extraction terminée avec succès !
          </div>
          {extractionInfo && (
            <div style={{fontSize:13, color:"#555", marginBottom:10}}>
              <div>📊 Type: {extractionInfo.direction}</div>
              <div>📁 Fichiers traités: {extractionInfo.filesCount}</div>
              <div>🕐 Date: {extractionInfo.timestamp}</div>
            </div>
          )}
          <div style={{display:"flex", gap:10, alignItems:"center"}}>
            <button 
              onClick={handleDownload}
              style={{
                padding:"10px 20px",
                backgroundColor:"#4caf50",
                color:"white",
                border:"none",
                borderRadius:5,
                cursor:"pointer",
                fontWeight:"bold"
              }}
            >
              📥 Télécharger le fichier Excel
            </button>
            <button 
              onClick={handleNewExtraction}
              style={{
                padding:"10px 20px",
                backgroundColor:"#2196f3",
                color:"white",
                border:"none",
                borderRadius:5,
                cursor:"pointer"
              }}
            >
              🔄 Nouvelle extraction
            </button>
          </div>
          <div style={{marginTop:8, fontSize:12, color:"#666", fontStyle:"italic"}}>
            Le fichier reste disponible jusqu'à ce que vous lanciez une nouvelle extraction.
          </div>
        </div>
      )}
    </div>
  );
}
