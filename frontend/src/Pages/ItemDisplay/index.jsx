import React, {useState, useEffect} from 'react';
import { useParams } from 'react-router-dom';

import { get, post } from '../../helper';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Box from '@mui/material/Box';
import CssBaseline from '@mui/material/CssBaseline';
import Container from '@mui/material/Container';

import Item from '../../Components/Item'



export default function Dashboard(){


    const {ListingID} = useParams();

    const [Items, setItems] = useState()
    const [Loading, setLoading] = useState(true)

    useEffect(()=>{

     get("/user/").then((res)=>{
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

            <div className='flex-col '>
                {!Loading && Items.map((n)=> (<Item item = {n}></Item>))}
            </div>
    </Container>

    
    )
}