import React, {useState, useEffect} from 'react';
import { get, post } from '../../helper';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Box from '@mui/material/Box';
import CssBaseline from '@mui/material/CssBaseline';
import Container from '@mui/material/Container';

import Item from '../../Components/Item'



export default function Dashboard(){

    const [Items, setItems] = useState()
    const [Loading, setLoading] = useState(true)

    useEffect(()=>{

     get("/item/get-items").then((res)=>{
        if(res.success){
            setItems(res.data)
            setLoading(false)
            console.log(Items)
        }

     })

    }, [Loading])

    const addItem = (e)=> {
        e.preventDefault();

        const data = new FormData(e.currentTarget)
        const body = {
            name:data.get("name"), 
            originUrl:data.get("originUrl"),
            currUrl: data.get("currUrl")? data.get("currUrl"):data.get("originUrl")
        }

        
        post("/item/create-item", body).then((res)=>{

            console.log(res)
            if(res.success){
                window.location.reload(false);

                setLoading(true)
            }
        })
    
    
    }



    return (


    <Container component="main" maxWidth="xl">
        <CssBaseline />
        <Box
          sx={{
            marginTop: 8,
            display: 'flex',
            flexDirection: 'row',

            justifyContent:"space-between",
          }}
        >        
            <div className='flex-col '>
                {!Loading && Items.map((n)=> (<Item item = {n}></Item>))}
            </div>

            <Box component="form" onSubmit={addItem} noValidate sx={{ mt: 1, maxWidth:390}}>
                <TextField
                margin="normal"
                required
                fullWidth
                id="name"
                label="Name"
                name="name"
                autoComplete="name"
                autoFocus
                />
                
                <TextField
                margin="normal"
                required
                fullWidth
                name="originUrl"
                label="originUrl"
                type="originUrl"
                id="originUrl"
                autoComplete="originUrl"
                />

                <TextField
                margin="normal"
                fullWidth
                name="currUrl"
                label="currUrl"
                type="currUrl"
                id="currUrl"
                autoComplete="currUrl"
                />
                <Button type="submit" variant="contained" sx={{ mt: 3, mb: 2 }}>Add Item</Button>

            </Box>
        </Box>
    </Container>

    
    )
}