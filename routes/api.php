<?php

use App\Http\Controllers\LinkController;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Route;

Route::get('/user', function (Request $request) {
    return "hola";
});

Route::post("/v1/link",[LinkController::class, "store"])->name("link.store");
Route::get("/v1/link/{link}",[LinkController::class, "get"])->name("link.get");
Route::get("/v1/link/",[LinkController::class, "index"])->name("link.index");
