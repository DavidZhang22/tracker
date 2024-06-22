import { Routes, BrowserRouter as Router, Route } from "react-router-dom";
import Login from "./Pages/Login"
import Navbar from "./Pages/Navbar"
import Signup from "./Pages/Signup"
import Dashboard from "./Pages/Dashboard"
import ItemDisplay from "./Pages/ItemDisplay"

function App() {
  return (
    <div className = "w-screen h-100">
    <Router>
    <Navbar></Navbar>
    <Routes>
      <Route path='/'  element={<Login/>} />
      <Route exact path='login' element={<Login/>} />
      <Route exact path='signup' element={<Signup/>} />
      <Route exact path='dashboard' element={<Dashboard/>} />
      <Route exact path='dashboard/:ItemID' element={<ItemDisplay/>} />

    </Routes>
    </Router>

    </div>
  );
}

export default App;
