import React, {useState, useEffect}  from 'react'

import Box from '@mui/material/Box';

export default function Item(props){

    const [data, setData] = useState(null)

    useEffect(()=>{

        if(props._id){

        }else if(props.item){
            setData(props.item)
        }
        
    }, [props])


    return(


        <div class='relative border-2 border-slate-150 bg-color-blue text-center w-auto'>
        
          {!!data && <h1 className='text-2xl font-mono p-10 '>{data.name}</h1>}

        </div>

    )




}