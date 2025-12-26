import React, {useState} from "react";
import axios from "axios";

export default function Login({onToken}){
  const [username,setUsername]=useState("alice");
  const [password,setPassword]=useState("alicepass");
  const [err,setErr]=useState(null);

  const submit = async (e) => {
    e.preventDefault();
    const form = new FormData();
    form.append('username', username);
    form.append('password', password);
    try {
      const res = await axios.post('/token', form);
      onToken(res.data.access_token);
    } catch (err) {
      setErr(err?.response?.data?.detail || err.message);
    }
  };

  return (
    <form onSubmit={submit} style={{maxWidth:420}}>
      <div>
        <label>Username</label>
        <input value={username} onChange={e=>setUsername(e.target.value)} />
      </div>
      <div>
        <label>Password</label>
        <input type="password" value={password} onChange={e=>setPassword(e.target.value)} />
      </div>
      <div style={{marginTop:10}}>
        <button type="submit">Login</button>
      </div>
      {err && <div style={{color:"red"}}>{err}</div>}
    </form>
  );
}
