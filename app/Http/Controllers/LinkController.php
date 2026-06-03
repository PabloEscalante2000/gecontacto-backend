<?php

namespace App\Http\Controllers;

use App\Http\Requests\StoreLinkRequest;
use App\Http\Resources\LinkResource;
use App\Models\Link;
use Illuminate\Http\Request;
use Termwind\Components\Li;

class LinkController extends Controller
{
    public function store(StoreLinkRequest $request){
        $link = Link::create([
            "phone" => $request["data.attributes.phone"],
            "message" => $request["data.attributes.message"]
        ]);
        return new LinkResource($link);
    }

    public function get(Link $link){
        $url = 'https://wa.me/' . $link->phone . '?text=' . urlencode($link->message);
        return redirect($url);
    }

    public function index() {
        return LinkResource::collection(Link::all());
    }
}
