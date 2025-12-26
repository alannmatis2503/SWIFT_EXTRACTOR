import React, {useState} from "react";
import Login from "./components/Login";
import Upload from "./components/Upload";

export default function App(){
  const [token, setToken] = useState(null);
  return (
    <div style={{padding:20, fontFamily: "Inter, Arial"}}>
      <h1>PDF SWIFT Extractor</h1>
      {!token ? <Login onToken={setToken} /> : <Upload token={token} onLogout={()=>setToken(null)} />}
    </div>
  );
}
